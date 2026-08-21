"""
MemoryMessageRepository — memory_messages 表的数据访问层

职责：
- 封装 memory_messages 表的 CRUD 操作
- 提供 write_cursor 原子推进（仅 agent/workflow 路径）
- 提供批量写入能力，支持两条独立的 seq 序列：
    * agent / workflow：按 conversation_id 分组
    * service_api / mcp：按 (end_user_id, source) 分组，用 pg_advisory_xact_lock 串行化
- 供 MemoryWriteDispatcher 共用
"""

import logging
import uuid
from contextlib import contextmanager
from typing import List, Optional

import sqlalchemy as sa
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.memory.enums import MemoryMessageSource
from app.core.utils.datetime_utils import ensure_dialog_at, to_iso_z, utcnow_naive
from app.models.conversation_model import Conversation
from app.models.memory_message_model import MemoryMessage

logger = logging.getLogger(__name__)


class MemoryMessageRepository:
    """memory_messages 表的数据访问层。

    提供消息写入、查询、游标推进等操作，供各入口点（API Service、Agent、
    Workflow、Flush、MCP）复用。
    """

    def __init__(self, db: Session | AsyncSession):
        self.db = db

    # ──────────────────────────────────────────────
    # 内部工具：seq 分配与并发锁
    # ──────────────────────────────────────────────

    @contextmanager
    def _acquire_mm_seq_lock(
        self,
        conversation_id: Optional[str],
        end_user_id: str,
        source: MemoryMessageSource,
    ):
        """在 seq 分组维度串行化 seq 分配，防止并发请求抢到重复 seq。

        使用 pg_advisory_xact_lock，事务提交/回滚时自动释放：
        - 有 conversation_id（agent/workflow）→ 锁 key 按 conversation_id
        - 无 conversation_id（service_api/mcp）→ 锁 key 按 (end_user_id, source)

        不同分组之间互不阻塞。详见设计文档 §3.3。
        """
        if conversation_id is not None:
            lock_key = f"mm_seq:conv:{conversation_id}"
        else:
            lock_key = f"mm_seq:{end_user_id}:{source.value}"
        self.db.execute(
            sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": lock_key},
        )
        yield

    def _next_seq(
        self,
        *,
        conversation_id: Optional[str],
        end_user_id: str,
        source: MemoryMessageSource,
    ) -> int:
        """按写入路径分流查询 max(message_seq)。

        - 有 conversation_id：按 conversation_id 查（agent/workflow 原逻辑）
        - conversation_id 为 NULL：按 (end_user_id, source) 查（API/MCP 新逻辑）
        """
        stmt = select(func.coalesce(func.max(MemoryMessage.message_seq), 0))
        if conversation_id is not None:
            stmt = stmt.where(MemoryMessage.conversation_id == uuid.UUID(conversation_id))
        else:
            stmt = stmt.where(
                MemoryMessage.conversation_id.is_(None),
                MemoryMessage.end_user_id == end_user_id,
                MemoryMessage.source == source.value,
            )
        return int(self.db.execute(stmt).scalar() or 0)

    # ──────────────────────────────────────────────
    # 消息写入
    # ──────────────────────────────────────────────

    def write_batch(
        self,
        conversation_id: Optional[str],
        messages: List[dict],
        *,
        end_user_id: str,
        source: MemoryMessageSource = MemoryMessageSource.AGENT,
    ) -> List[dict]:
        """批量写入 memory_messages 表，自动分配递增 message_seq。

        在单个 DB 事务中完成：查询 max(message_seq) → 逐条分配 + 写入。
        调用方需自行 commit。

        Args:
            conversation_id: 对话 ID。agent/workflow 传字符串；service_api/mcp 传 None
            messages: 消息列表，每条格式 {"role": "user"|"assistant", "content": "...", "files": [...]}
            end_user_id: 终端用户 ID（必填）
            source: 写入来源枚举，决定 seq 分组键与 memory_messages.source 字段值

        Returns:
            成功写入的消息摘要列表
            [{"role": "user", "message_seq": 1, "content": "...", "dialog_at": ..., "files": ...,
              "original_message_id": "uuid-str" | None}, ...]
            （跳过 content 为空的消息）
        """
        # 所有路径统一持锁分配 seq，防止同一分组的并发请求抢到重复 seq
        with self._acquire_mm_seq_lock(conversation_id, end_user_id, source):
            return self._write_batch_inner(
                conversation_id, messages, end_user_id, source,
            )

    def _write_batch_inner(
        self,
        conversation_id: Optional[str],
        messages: List[dict],
        end_user_id: str,
        source: MemoryMessageSource,
    ) -> List[dict]:
        written: List[dict] = []
        next_seq = self._next_seq(
            conversation_id=conversation_id,
            end_user_id=end_user_id,
            source=source,
        )

        conv_uuid = uuid.UUID(conversation_id) if conversation_id else None

        for msg in messages:
            role = str(msg.get("role", "user"))
            content = str(msg.get("content", "") or "")
            if not content.strip():
                continue

            next_seq += 1
            memory_message = MemoryMessage(
                id=uuid.uuid4(),
                conversation_id=conv_uuid,
                original_message_id=msg.get("original_message_id"),
                end_user_id=end_user_id,
                source=source.value,
                role=role,
                content=content,
                message_seq=next_seq,
                should_memorize=msg.get("should_memorize", True),
                created_at=msg.get("created_at") or utcnow_naive(),
                dialog_at=ensure_dialog_at(msg.get("dialog_at")),
                files=msg.get("files"),
            )
            self.db.add(memory_message)
            written.append({
                "memory_message_id": str(memory_message.id),
                "role": role,
                "source": source.value,
                "message_seq": next_seq,
                "content": content,
                "dialog_at": memory_message.dialog_at,
                "files": memory_message.files,
                # 快写侧用它作为情绪缓存 key（= 回复链路的 user_message_id）；
                "original_message_id": (
                    str(memory_message.original_message_id)
                    if memory_message.original_message_id else None
                ),
            })
            logger.debug(
                "[MemoryMessageRepository] 写入 memory_messages: "
                f"conv={conversation_id or 'NULL'}, source={source.value}, "
                f"end_user={end_user_id}, seq={next_seq}, role={role}"
            )

        return written

    # ──────────────────────────────────────────────
    # 消息查询
    # ──────────────────────────────────────────────

    def get_by_seq(
        self,
        conversation_id: str,
        message_seq: int,
    ) -> Optional[MemoryMessage]:
        """按 message_seq 查询单条消息。"""
        return self.db.execute(
            select(MemoryMessage)
            .where(
                MemoryMessage.conversation_id == conversation_id,
                MemoryMessage.message_seq == message_seq,
            )
        ).scalar_one_or_none()

    def get_pending_messages(
        self,
        conversation_id: str,
        write_cursor: int,
        role: Optional[str] = None,
    ) -> List[MemoryMessage]:
        """查询 message_seq > write_cursor 的待处理消息（agent/workflow 路径）。"""
        stmt = (
            select(MemoryMessage)
            .where(
                MemoryMessage.conversation_id == conversation_id,
                MemoryMessage.message_seq > write_cursor,
            )
            .order_by(MemoryMessage.message_seq.asc())
        )
        if role:
            stmt = stmt.where(MemoryMessage.role == role)

        return list(self.db.scalars(stmt).all())

    def get_user_seqs(self, conversation_id: str) -> List[int]:
        """获取对话中所有 role=user 的 message_seq 升序列表。"""
        rows = self.db.execute(
            select(MemoryMessage.message_seq)
            .where(
                MemoryMessage.conversation_id == conversation_id,
                MemoryMessage.role == "user",
            )
            .order_by(MemoryMessage.message_seq.asc())
        ).scalars().all()
        return [int(s) for s in rows if s is not None]

    def get_max_seq(self, conversation_id: str) -> Optional[int]:
        """获取对话中最大的 message_seq。"""
        return self.db.execute(
            select(func.max(MemoryMessage.message_seq))
            .where(MemoryMessage.conversation_id == conversation_id)
        ).scalar()

    # ──────────────────────────────────────────────
    # 工作记忆查询（API/MCP 展示接口）
    # ──────────────────────────────────────────────

    _API_MCP_SOURCES = ("service_api", "mcp")

    def get_working_memory_sources(self, end_user_id: str) -> List[dict]:
        """返回该用户 API/MCP 各来源的记忆摘要（每 source 一行）。

        Returns:
            [{"source": "service_api", "message_count": 30, "latest_at": datetime}, ...]
        """
        rows = self.db.execute(
            select(
                MemoryMessage.source,
                func.count().label("message_count"),
                func.max(MemoryMessage.created_at).label("latest_at"),
            )
            .where(
                MemoryMessage.end_user_id == end_user_id,
                MemoryMessage.conversation_id.is_(None),
                MemoryMessage.source.in_(self._API_MCP_SOURCES),
            )
            .group_by(MemoryMessage.source)
        ).all()
        return [
            {"source": r.source, "message_count": int(r.message_count), "latest_at": r.latest_at}
            for r in rows
        ]

    async def get_working_memory_sources_async(self, end_user_id: str) -> List[dict]:
        """返回该用户 API/MCP 各来源的记忆摘要（每 source 一行）（异步版本）。"""
        result = await self.db.execute(
            select(
                MemoryMessage.source,
                func.count().label("message_count"),
                func.max(MemoryMessage.created_at).label("latest_at"),
            )
            .where(
                MemoryMessage.end_user_id == end_user_id,
                MemoryMessage.conversation_id.is_(None),
                MemoryMessage.source.in_(self._API_MCP_SOURCES),
            )
            .group_by(MemoryMessage.source)
        )
        rows = result.all()
        return [
            {"source": r.source, "message_count": int(r.message_count), "latest_at": r.latest_at}
            for r in rows
        ]

    async def get_working_memory_source_count_async(self, end_user_id: str) -> int:
        """统计该用户 API/MCP 来源的 distinct source 数量（异步版本）。

        返回值等价于 len(get_working_memory_sources(...))。
        """
        rows = await self.db.execute(
            select(MemoryMessage.source)
            .where(
                MemoryMessage.end_user_id == end_user_id,
                MemoryMessage.conversation_id.is_(None),
                MemoryMessage.source.in_(self._API_MCP_SOURCES),
            )
            .group_by(MemoryMessage.source)
        )
        return len(rows.all())

    def has_api_mcp_messages(self, end_user_id: str) -> bool:
        """判断该用户是否有任何 API/MCP 来源的记忆消息（用于 work_count +1 判断）。"""
        exists = self.db.execute(
            select(MemoryMessage.id)
            .where(
                MemoryMessage.end_user_id == end_user_id,
                MemoryMessage.conversation_id.is_(None),
                MemoryMessage.source.in_(self._API_MCP_SOURCES),
            )
            .limit(1)
        ).scalar_one_or_none()
        return exists is not None

    async def list_recent_messages_by_source_async(
        self,
        end_user_id: str,
        source: str,
        page: int = 1,
        pagesize: int = 20,
    ) -> tuple[list[MemoryMessage], int]:
        """按来源分页获取消息 + 该来源总条数（异步版本）。

        分页语义（service_api / mcp 两种来源完全一致）：
        - 使用 page / pagesize 分页，page 从 1 开始
        - 按 message_seq ASC 排序：message_seq 从小到大表示消息从旧到新，
          且在该来源分组内单调递增，保证翻页无重复、无遗漏
        - total 通过窗口函数 count(*) OVER () 与数据同一条 SQL 返回，
          避免独立的 COUNT 往返；页码越界时回退一次 COUNT 保证 total 准确
        """
        base_filter = [
            MemoryMessage.end_user_id == end_user_id,
            MemoryMessage.conversation_id.is_(None),
            MemoryMessage.source == source,
        ]

        offset = (page - 1) * pagesize
        rows_result = await self.db.execute(
            select(
                MemoryMessage,
                func.count().over().label("total"),
            )
            .where(*base_filter)
            .order_by(MemoryMessage.message_seq.asc())
            .offset(offset)
            .limit(pagesize)
        )
        rows_with_total = rows_result.all()

        if not rows_with_total:
            # 该页无数据：可能是该来源本身无消息，也可能是页码越界。
            # 补一次 COUNT 返回真实 total，避免元数据失真。
            total_result = await self.db.execute(
                select(func.count(MemoryMessage.id)).where(*base_filter)
            )
            return [], int(total_result.scalar_one())

        total = int(rows_with_total[0].total)
        rows = [row[0] for row in rows_with_total]

        return rows, total

    # ──────────────────────────────────────────────
    # write_cursor 操作（仅服务 agent/workflow 路径）
    # ──────────────────────────────────────────────

    def get_write_cursor(self, conversation_id: str) -> Optional[int]:
        """查询对话的 write_cursor。"""
        return self.db.execute(
            select(Conversation.write_cursor)
            .where(Conversation.id == conversation_id)
        ).scalar_one_or_none()

    def advance_write_cursor(
        self,
        conversation_id: str,
        message_seq: int,
    ) -> bool:
        """原子推进 write_cursor（单调递增）。

        UPDATE conversations SET write_cursor = :seq
        WHERE id = :conv_id AND write_cursor < :seq

        Returns:
            是否成功推进（True 表示推进了，False 表示 cursor 已 >= seq 或 conversation 不存在）
        """
        result = self.db.execute(
            update(Conversation)
            .where(
                Conversation.id == conversation_id,
                Conversation.write_cursor < message_seq,
            )
            .values(write_cursor=message_seq)
        )
        return result.rowcount > 0

    def verify_cursor_complete(self, conversation_id: str) -> bool:
        """验证 write_cursor >= max(message_seq)，即所有消息都已处理。"""
        max_seq = self.get_max_seq(conversation_id)
        cursor = self.get_write_cursor(conversation_id) or 0
        return max_seq is None or max_seq <= cursor

    # ──────────────────────────────────────────────
    # 上下文窗口查询（agent/workflow 滑动窗口路径使用）
    # ──────────────────────────────────────────────

    def build_context_before(
        self,
        conversation_id: str,
        target_seq: int,
        window_size: int = 3,
    ) -> List[MemoryMessage]:
        """构建上文消息列表。

        向前查找最多 window_size 个 user 消息，取最小 message_seq 作为上边界，
        查询 [upper_bound, target_seq) 范围内所有消息。
        """
        # 向前查找 user 消息 seq
        upstream_q_seqs = self.db.execute(
            select(MemoryMessage.message_seq)
            .where(
                MemoryMessage.conversation_id == conversation_id,
                MemoryMessage.role == "user",
                MemoryMessage.message_seq < target_seq,
            )
            .order_by(MemoryMessage.message_seq.desc())
            .limit(window_size)
        ).scalars().all()

        if not upstream_q_seqs:
            return []

        upper_bound = min(upstream_q_seqs)

        return list(
            self.db.execute(
                select(MemoryMessage)
                .where(
                    MemoryMessage.conversation_id == conversation_id,
                    MemoryMessage.message_seq >= upper_bound,
                    MemoryMessage.message_seq < target_seq,
                )
                .order_by(MemoryMessage.message_seq.asc())
            ).scalars().all()
        )

    def build_context_after(
        self,
        conversation_id: str,
        target_seq: int,
        window_size: int = 3,
    ) -> List[MemoryMessage]:
        """构建下文消息列表。

        向后查找最多 window_size 个 user 消息，取最大 message_seq 作为下边界，
        查询 (target_seq, lower_bound] 范围内所有消息。若下游无 user 消息，
        则返回 target_seq 之后的所有消息（包含尾部 assistant 消息），
        确保最后一条 assistant 消息也能作为上下文被处理。
        """
        downstream_q_seqs = self.db.execute(
            select(MemoryMessage.message_seq)
            .where(
                MemoryMessage.conversation_id == conversation_id,
                MemoryMessage.role == "user",
                MemoryMessage.message_seq > target_seq,
            )
            .order_by(MemoryMessage.message_seq.asc())
            .limit(window_size)
        ).scalars().all()

        if not downstream_q_seqs:
            # 下游无 user 消息，返回 target_seq 之后的所有消息（尾部 assistant 消息）
            return list(
                self.db.execute(
                    select(MemoryMessage)
                    .where(
                        MemoryMessage.conversation_id == conversation_id,
                        MemoryMessage.message_seq > target_seq,
                    )
                    .order_by(MemoryMessage.message_seq.asc())
                ).scalars().all()
            )

        lower_bound = max(downstream_q_seqs)

        return list(
            self.db.execute(
                select(MemoryMessage)
                .where(
                    MemoryMessage.conversation_id == conversation_id,
                    MemoryMessage.message_seq > target_seq,
                    MemoryMessage.message_seq <= lower_bound,
                )
                .order_by(MemoryMessage.message_seq.asc())
            ).scalars().all()
        )

    def batch_update_pruned_content(
        self,
        updates: List[tuple],
        conversation_id: Optional[str] = None,
        end_user_id: str = "",
        source: str = "",
    ) -> None:
        """批量回写 pruned_content 和 topic_entity_hint 到 memory_messages 表。

        使用 CASE WHEN 单条 SQL 完成多行更新，避免 N 次 roundtrip。

        Args:
            updates: [(message_seq, pruned_content, topic_entity_hint), ...] 列表
                     兼容旧格式 (message_seq, pruned_content) — topic_entity_hint 视为 None
            conversation_id: 对话 ID（agent/workflow 路径）
            end_user_id: 终端用户 ID（API/MCP 路径）
            source: 写入来源（API/MCP 路径）
        """
        if not updates:
            return

        seqs = [u[0] for u in updates]

        # 构建 pruned_content CASE WHEN 表达式
        content_whens = {u[0]: u[1] for u in updates}
        content_case = sa.case(
            content_whens,
            value=MemoryMessage.message_seq,
        )

        # 构建 topic_entity_hint CASE WHEN 表达式（只包含非 None 的值，避免冗余 WHEN seq THEN NULL）
        hint_whens = {u[0]: u[2] for u in updates if len(u) > 2 and u[2] is not None}

        # 构建 WHERE 条件
        if conversation_id:
            conv_uuid = uuid.UUID(conversation_id)
            where_clause = sa.and_(
                MemoryMessage.conversation_id == conv_uuid,
                MemoryMessage.message_seq.in_(seqs),
            )
        else:
            where_clause = sa.and_(
                MemoryMessage.end_user_id == end_user_id,
                MemoryMessage.source == source,
                MemoryMessage.conversation_id.is_(None),
                MemoryMessage.message_seq.in_(seqs),
            )

        values = {"pruned_content": content_case}
        if hint_whens:
            hint_case = sa.case(
                hint_whens,
                value=MemoryMessage.message_seq,
            )
            values["topic_entity_hint"] = hint_case

        self.db.execute(
            update(MemoryMessage)
            .where(where_clause)
            .values(**values)
        )

    def batch_get_pruned_content(
        self,
        seqs: List[int],
        conversation_id: Optional[str] = None,
        end_user_id: str = "",
        source: str = "",
    ) -> dict:
        """批量查询指定消息的 pruned_content 和 topic_entity_hint（仅返回非 NULL 的结果）。

        用于 WritePipeline 执行时刷新 dispatcher 快照中尚未回写的 pruned_content。

        Args:
            seqs: 需要查询的 message_seq 列表
            conversation_id: 对话 ID（agent/workflow 路径）
            end_user_id: 终端用户 ID（API/MCP 路径）
            source: 写入来源（API/MCP 路径）

        Returns:
            {message_seq: {"pruned_content": str, "topic_entity_hint": str|None}} 字典，
            只包含 pruned_content 非 NULL 的行
        """
        if not seqs:
            return {}

        if conversation_id:
            conv_uuid = uuid.UUID(conversation_id)
            where_clause = sa.and_(
                MemoryMessage.conversation_id == conv_uuid,
                MemoryMessage.message_seq.in_(seqs),
                MemoryMessage.pruned_content.isnot(None),
            )
        else:
            where_clause = sa.and_(
                MemoryMessage.end_user_id == end_user_id,
                MemoryMessage.source == source,
                MemoryMessage.conversation_id.is_(None),
                MemoryMessage.message_seq.in_(seqs),
                MemoryMessage.pruned_content.isnot(None),
            )

        rows = self.db.execute(
            select(MemoryMessage.message_seq, MemoryMessage.pruned_content, MemoryMessage.topic_entity_hint)
            .where(where_clause)
        ).all()

        return {
            row.message_seq: {
                "pruned_content": row.pruned_content,
                "topic_entity_hint": row.topic_entity_hint,
            }
            for row in rows
        }


def message_to_dict(message: MemoryMessage) -> dict:
    """将 MemoryMessage ORM 对象转换为字典格式。"""
    return {
        "memory_message_id": str(message.id),
        "role": message.role,
        "source": message.source,
        "content": message.content,
        "message_seq": message.message_seq,
        "should_memorize": message.should_memorize,
        "created_at": to_iso_z(message.created_at) if message.created_at else None,
        "dialog_at": message.dialog_at,
        "files": message.files,
        "pruned_content": message.pruned_content,
        "topic_entity_hint": message.topic_entity_hint,
    }
