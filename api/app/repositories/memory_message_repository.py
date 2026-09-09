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
from datetime import datetime
from typing import List, Optional

import sqlalchemy as sa
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, aliased
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
            # 用 uuid.UUID() 归一化（大小写/连字符），与 _next_seq 的分组键对齐，
            # 避免同一 conversation 因字符串格式不一致拿到不同锁导致并发撞号。
            lock_key = f"mm_seq:conv:{uuid.UUID(conversation_id)}"
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

    def get_seq_group(
        self,
        conversation_id: str,
        message_seq: int,
    ) -> List[MemoryMessage]:
        """返回同一 (conversation_id, message_seq) 的全部行，按入库时间升序。

        正常状态每组仅 1 行；历史脏数据可能同 seq 多行。按 (created_at, id)
        升序保证确定性，供“撞号整组逐行派发”使用：先入库的排前面（1、2、3…）。
        """
        return list(
            self.db.scalars(
                select(MemoryMessage)
                .where(
                    MemoryMessage.conversation_id == conversation_id,
                    MemoryMessage.message_seq == message_seq,
                )
                .order_by(
                    MemoryMessage.created_at.asc(),
                    MemoryMessage.id.asc(),
                )
            ).all()
        )

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
        keyword: str | None = None,
        start_at: datetime | None = None,
        end_at_exclusive: datetime | None = None,
    ) -> tuple[list[MemoryMessage], int]:
        """Fetch a filtered page of messages and the matching total for a source.

        Args:
            end_user_id: End-user identifier owning the messages.
            source: Message source to match.
            page: One-based page number.
            pagesize: Maximum messages per page.
            keyword: Optional keyword matched against message content.
            start_at: Optional inclusive creation-time lower bound.
            end_at_exclusive: Optional exclusive creation-time upper bound.

        Returns:
            The messages ordered by creation time and the filtered total count.
        """
        base_filter = [
            MemoryMessage.end_user_id == end_user_id,
            MemoryMessage.conversation_id.is_(None),
            MemoryMessage.source == source,
        ]
        if keyword is not None:
            base_filter.append(MemoryMessage.content.icontains(keyword, autoescape=True))
        if start_at is not None:
            base_filter.append(MemoryMessage.created_at >= start_at)
        if end_at_exclusive is not None:
            base_filter.append(MemoryMessage.created_at < end_at_exclusive)

        offset = (page - 1) * pagesize
        rows_result = await self.db.execute(
            select(
                MemoryMessage,
                func.count().over().label("total"),
            )
            .where(*base_filter)
            .order_by(MemoryMessage.created_at.asc())
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
                .order_by(
                    MemoryMessage.message_seq.asc(),
                    MemoryMessage.created_at.asc(),
                    MemoryMessage.id.asc(),
                )
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
                    .order_by(
                        MemoryMessage.message_seq.asc(),
                        MemoryMessage.created_at.asc(),
                        MemoryMessage.id.asc(),
                    )
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
                .order_by(
                    MemoryMessage.message_seq.asc(),
                    MemoryMessage.created_at.asc(),
                    MemoryMessage.id.asc(),
                )
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


    # ──────────────────────────────────────────────
    # Scene boundary / SceneSummary queries
    # ──────────────────────────────────────────────

    @staticmethod
    def _scene_effective_filters():
        return (
            MemoryMessage.should_memorize.is_(True),
            MemoryMessage.role.in_(("user", "assistant")),
            func.length(func.trim(MemoryMessage.content)) > 0,
        )

    @staticmethod
    def _scene_summary_ready_filters():
        """摘要扫描忽略尚未完成边界判断的 user 消息。"""
        return (
            *MemoryMessageRepository._scene_effective_filters(),
            sa.or_(
                MemoryMessage.role == "assistant",
                MemoryMessage.scene_boundary.is_not(None),
            ),
        )

    @staticmethod
    def _before(message: MemoryMessage):
        return sa.tuple_(MemoryMessage.created_at, MemoryMessage.id) < sa.tuple_(
            message.created_at, message.id
        )

    @staticmethod
    def _scene_stream_filters(message: MemoryMessage):
        filters = [MemoryMessage.end_user_id == message.end_user_id]
        if message.conversation_id is not None:
            filters.append(MemoryMessage.conversation_id == message.conversation_id)
        else:
            filters.extend((
                MemoryMessage.conversation_id.is_(None),
                MemoryMessage.source == message.source,
            ))
        return tuple(filters)

    def get_scene_context(
        self,
        *,
        resolved_end_user_id: str,
        memory_message_id: str,
        history_window_size: int,
    ) -> dict | None:
        current = self.db.execute(
            select(MemoryMessage).where(
                MemoryMessage.id == uuid.UUID(str(memory_message_id)),
                MemoryMessage.end_user_id == resolved_end_user_id,
                MemoryMessage.role == "user",
                MemoryMessage.should_memorize.is_(True),
                func.length(func.trim(MemoryMessage.content)) > 0,
            )
        ).scalar_one_or_none()
        if current is None:
            return None

        stream_filters = self._scene_stream_filters(current)
        previous_valid = self.db.execute(
            select(MemoryMessage)
            .where(
                *stream_filters,
                self._before(current),
                *self._scene_effective_filters(),
            )
            .order_by(MemoryMessage.created_at.desc(), MemoryMessage.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        shifted = self.db.execute(
            select(MemoryMessage)
            .where(
                *stream_filters,
                MemoryMessage.role == "user",
                MemoryMessage.should_memorize.is_(True),
                MemoryMessage.scene_boundary == "SHIFTED",
                self._before(current),
            )
            .order_by(MemoryMessage.created_at.desc(), MemoryMessage.id.desc())
            .limit(1)
        ).scalar_one_or_none()

        scene_filter = []
        if shifted is not None:
            scene_filter.append(
                sa.tuple_(MemoryMessage.created_at, MemoryMessage.id)
                >= sa.tuple_(shifted.created_at, shifted.id)
            )
        user_count = int(
            self.db.execute(
                select(func.count(MemoryMessage.id)).where(
                    *stream_filters,
                    MemoryMessage.role == "user",
                    MemoryMessage.should_memorize.is_(True),
                    MemoryMessage.scene_boundary.in_(("SHIFTED", "CONTINUE", "BERT_FAILED_CONTINUE")),
                    func.length(func.trim(MemoryMessage.content)) > 0,
                    self._before(current),
                    *scene_filter,
                )
            ).scalar_one()
        )
        user_rows = list(
            self.db.execute(
                select(MemoryMessage)
                .where(
                    *stream_filters,
                    MemoryMessage.role == "user",
                    MemoryMessage.should_memorize.is_(True),
                    MemoryMessage.scene_boundary.in_(("SHIFTED", "CONTINUE", "BERT_FAILED_CONTINUE")),
                    func.length(func.trim(MemoryMessage.content)) > 0,
                    self._before(current),
                    *scene_filter,
                )
                .order_by(MemoryMessage.created_at.desc(), MemoryMessage.id.desc())
                .limit(max(history_window_size, 1))
            ).scalars().all()
        )
        return {
            "current_content": current.content,
            "current_created_at": current.created_at,
            "previous_shifted_message_id": str(shifted.id) if shifted else None,
            "previous_message_created_at": previous_valid.created_at if previous_valid else None,
            "current_scene_turn_count": user_count,
            "history_user_messages": [row.content for row in reversed(user_rows)],
            "existing_boundary": current.scene_boundary,
        }

    def cas_scene_boundary(
        self,
        *,
        memory_message_id: str,
        resolved_end_user_id: str,
        decision: str,
    ) -> tuple[bool, str | None]:
        conditions = [
            MemoryMessage.id == uuid.UUID(str(memory_message_id)),
            MemoryMessage.end_user_id == resolved_end_user_id,
            MemoryMessage.role == "user",
            MemoryMessage.should_memorize.is_(True),
            MemoryMessage.scene_boundary.is_(None),
        ]
        result = self.db.execute(
            update(MemoryMessage).where(*conditions).values(scene_boundary=decision)
        )
        if result.rowcount:
            return True, decision
        current = self.db.execute(
            select(MemoryMessage.scene_boundary).where(
                MemoryMessage.id == uuid.UUID(str(memory_message_id)),
                MemoryMessage.end_user_id == resolved_end_user_id,
            )
        ).scalar_one_or_none()
        return False, current

    def claim_scene_summary(
        self,
        *,
        scene_start_message_id: str,
        end_user_id: str,
    ) -> bool:
        """原子领取一个 SHIFTED Scene 起点，保证摘要任务最多派发一次。"""
        result = self.db.execute(
            update(MemoryMessage)
            .where(
                MemoryMessage.id == uuid.UUID(str(scene_start_message_id)),
                MemoryMessage.end_user_id == end_user_id,
                MemoryMessage.role == "user",
                MemoryMessage.should_memorize.is_(True),
                MemoryMessage.scene_boundary == "SHIFTED",
                MemoryMessage.scene_summary_claimed_at.is_(None),
            )
            .values(scene_summary_claimed_at=utcnow_naive())
        )
        return bool(result.rowcount)

    def release_scene_summary_claim(
        self,
        *,
        scene_start_message_id: str,
        end_user_id: str,
    ) -> bool:
        """释放未真正进入生成流程的领取，使其可再次被 scanner 获取。"""
        result = self.db.execute(
            update(MemoryMessage)
            .where(
                MemoryMessage.id == uuid.UUID(str(scene_start_message_id)),
                MemoryMessage.end_user_id == end_user_id,
                MemoryMessage.role == "user",
                MemoryMessage.should_memorize.is_(True),
                MemoryMessage.scene_boundary == "SHIFTED",
                MemoryMessage.scene_summary_claimed_at.is_not(None),
            )
            .values(scene_summary_claimed_at=None)
        )
        return bool(result.rowcount)

    def load_scene_interval(
        self,
        *,
        end_user_id: str,
        scene_start_message_id: str,
        close_before_message_id: str | None,
        idle_high_watermark_message_id: str | None,
        idle_timeout_seconds: int,
        max_message_chars: int,
        close_reason: str,
    ) -> dict | None:
        start = self.db.execute(
            select(
                MemoryMessage.id,
                MemoryMessage.end_user_id,
                MemoryMessage.conversation_id,
                MemoryMessage.source,
                MemoryMessage.created_at,
                MemoryMessage.message_seq,
            ).where(
                MemoryMessage.id == uuid.UUID(scene_start_message_id),
                MemoryMessage.end_user_id == end_user_id,
                MemoryMessage.role == "user",
                MemoryMessage.should_memorize.is_(True),
                MemoryMessage.scene_boundary == "SHIFTED",
            )
        ).one_or_none()
        if start is None:
            return None

        stream_filters = self._scene_stream_filters(start)
        close = None
        if close_before_message_id:
            close = self.db.execute(
                select(
                    MemoryMessage.id,
                    MemoryMessage.created_at,
                    MemoryMessage.message_seq,
                ).where(
                    MemoryMessage.id == uuid.UUID(close_before_message_id),
                    *stream_filters,
                    MemoryMessage.role == "user",
                    MemoryMessage.should_memorize.is_(True),
                    MemoryMessage.scene_boundary == "SHIFTED",
                    sa.tuple_(
                        MemoryMessage.created_at,
                        MemoryMessage.message_seq,
                        MemoryMessage.id,
                    )
                    > sa.tuple_(start.created_at, start.message_seq, start.id),
                )
            ).one_or_none()
            if close is None:
                return None
        elif close_reason == "IDLE_TIMEOUT":
            close = self.db.execute(
                select(
                    MemoryMessage.id,
                    MemoryMessage.created_at,
                    MemoryMessage.message_seq,
                )
                .where(
                    *stream_filters,
                    MemoryMessage.role == "user",
                    MemoryMessage.should_memorize.is_(True),
                    MemoryMessage.scene_boundary == "SHIFTED",
                    sa.tuple_(
                        MemoryMessage.created_at,
                        MemoryMessage.message_seq,
                        MemoryMessage.id,
                    )
                    > sa.tuple_(start.created_at, start.message_seq, start.id),
                )
                .order_by(
                    MemoryMessage.created_at.asc(),
                    MemoryMessage.message_seq.asc(),
                    MemoryMessage.id.asc(),
                )
                .limit(1)
            ).one_or_none()

        conditions = [
            *stream_filters,
            sa.tuple_(
                MemoryMessage.created_at,
                MemoryMessage.message_seq,
                MemoryMessage.id,
            )
            >= sa.tuple_(start.created_at, start.message_seq, start.id),
            *(
                self._scene_summary_ready_filters()
                if close_reason == "IDLE_TIMEOUT" and close is None
                else self._scene_effective_filters()
            ),
        ]
        if close is not None:
            conditions.append(
                sa.tuple_(
                    MemoryMessage.created_at,
                    MemoryMessage.message_seq,
                    MemoryMessage.id,
                )
                < sa.tuple_(close.created_at, close.message_seq, close.id)
            )
        rows = list(
            self.db.execute(
                select(
                    MemoryMessage.id,
                    MemoryMessage.role,
                    func.left(
                        MemoryMessage.content,
                        max(int(max_message_chars), 1),
                    ).label("content"),
                    MemoryMessage.created_at,
                )
                .where(*conditions)
                .order_by(
                    MemoryMessage.created_at.asc(),
                    MemoryMessage.message_seq.asc(),
                    MemoryMessage.id.asc(),
                )
            ).all()
        )
        if not rows:
            return None
        if close_reason == "IDLE_TIMEOUT" and close is None:
            last = rows[-1]
            if idle_high_watermark_message_id != str(last.id):
                return None
            if (utcnow_naive() - last.created_at).total_seconds() < idle_timeout_seconds:
                return None
        return {
            "messages": [
                {
                    "id": str(row.id),
                    "role": row.role,
                    "content": row.content,
                    "created_at": row.created_at,
                }
                for row in rows
            ],
            "conversation_id": str(start.conversation_id) if start.conversation_id else None,
            "close_reason": "SHIFTED" if close is not None else close_reason,
        }

    @staticmethod
    def _latest_shifted_scene_start_statements(
        *,
        after_created_at=None,
        after_id=None,
        batch_size: int | None = None,
    ):
        start = aliased(MemoryMessage, name="scene_start")
        later = aliased(MemoryMessage, name="later_shift")

        shifted_filters = (
            start.role == "user",
            start.should_memorize.is_(True),
            start.scene_boundary == "SHIFTED",
            start.scene_summary_claimed_at.is_(None),
        )
        later_shifted = (
            select(later.id)
            .where(
                later.end_user_id == start.end_user_id,
                later.role == "user",
                later.should_memorize.is_(True),
                later.scene_boundary == "SHIFTED",
                sa.tuple_(later.created_at, later.id)
                > sa.tuple_(start.created_at, start.id),
                sa.or_(
                    sa.and_(
                        start.conversation_id.is_not(None),
                        later.conversation_id == start.conversation_id,
                    ),
                    sa.and_(
                        start.conversation_id.is_(None),
                        later.conversation_id.is_(None),
                        later.source == start.source,
                    ),
                ),
            )
            .exists()
        )
        columns = (
            start.id.label("id"),
            start.end_user_id.label("end_user_id"),
            start.conversation_id.label("conversation_id"),
            start.source.label("source"),
            start.created_at.label("created_at"),
        )

        def last_ready_message_lateral(name: str, *, conversation_stream: bool):
            message = aliased(MemoryMessage, name=f"{name}_message")
            stream_conditions = (
                (message.conversation_id == start.conversation_id,)
                if conversation_stream
                else (
                    message.conversation_id.is_(None),
                    message.source == start.source,
                )
            )
            return (
                select(
                    message.id.label("last_message_id"),
                    message.created_at.label("last_message_at"),
                )
                .where(
                    message.end_user_id == start.end_user_id,
                    *stream_conditions,
                    message.should_memorize.is_(True),
                    message.role.in_(("user", "assistant")),
                    func.length(func.trim(message.content)) > 0,
                    sa.or_(
                        message.role == "assistant",
                        message.scene_boundary.is_not(None),
                    ),
                    sa.tuple_(message.created_at, message.id)
                    >= sa.tuple_(start.created_at, start.id),
                )
                .order_by(message.created_at.desc(), message.id.desc())
                .limit(1)
                .correlate(start)
                .lateral(name)
            )

        cursor_filter = ()
        if after_created_at is not None and after_id is not None:
            cursor_filter = (
                sa.tuple_(start.created_at, start.id)
                > sa.tuple_(after_created_at, uuid.UUID(str(after_id))),
            )

        conversation_last = last_ready_message_lateral(
            "conversation_last",
            conversation_stream=True,
        )
        source_last = last_ready_message_lateral(
            "source_last",
            conversation_stream=False,
        )
        conversation_streams = (
            select(
                *columns,
                conversation_last.c.last_message_id,
                conversation_last.c.last_message_at,
            )
            .outerjoin(conversation_last, sa.true())
            .where(
                *shifted_filters,
                start.conversation_id.is_not(None),
                ~later_shifted,
                *cursor_filter,
            )
            .order_by(start.created_at.asc(), start.id.asc())
        )
        source_streams = (
            select(
                *columns,
                source_last.c.last_message_id,
                source_last.c.last_message_at,
            )
            .outerjoin(source_last, sa.true())
            .where(
                *shifted_filters,
                start.conversation_id.is_(None),
                ~later_shifted,
                *cursor_filter,
            )
            .order_by(start.created_at.asc(), start.id.asc())
        )
        if batch_size is not None:
            size = max(int(batch_size), 1)
            conversation_streams = conversation_streams.limit(size)
            source_streams = source_streams.limit(size)
        return conversation_streams, source_streams

    def _latest_shifted_scene_start_batch(
        self,
        *,
        after_created_at=None,
        after_id=None,
        batch_size: int,
    ) -> list:
        starts = []
        statements = self._latest_shifted_scene_start_statements(
            after_created_at=after_created_at,
            after_id=after_id,
            batch_size=batch_size,
        )
        for statement in statements:
            starts.extend(self.db.execute(statement).all())
        starts.sort(key=lambda row: (row.created_at, row.id))
        return starts[:batch_size]

    def _load_scene_configs(self, end_user_ids: set[str]) -> dict[str, object | None]:
        """一次查询加载当前批次涉及的 workspace Scene 配置。"""
        from app.models.end_user_model import EndUser
        from app.models.memory_config_model import MemoryConfig
        from app.models.workspace_model import Workspace

        valid_ids = []
        configs: dict[str, object | None] = {end_user_id: None for end_user_id in end_user_ids}
        for end_user_id in end_user_ids:
            try:
                valid_ids.append(uuid.UUID(end_user_id))
            except ValueError:
                continue
        if not valid_ids:
            return configs

        rows = self.db.execute(
            select(
                EndUser.id.label("end_user_id"),
                MemoryConfig.config_id.label("config_id"),
                MemoryConfig.scene_idle_timeout_seconds.label(
                    "scene_idle_timeout_seconds"
                ),
            )
            .join(Workspace, EndUser.workspace_id == Workspace.id)
            .join(MemoryConfig, Workspace.memory_config == MemoryConfig.config_id)
            .where(EndUser.id.in_(valid_ids))
        ).all()
        for row in rows:
            configs[str(row.end_user_id)] = row
        return configs

    def list_idle_scene_candidates(
        self,
        limit: int = 100,
        *,
        batch_size: int = 200,
        scan_budget: int = 2000,
        after_created_at=None,
        after_id=None,
    ) -> dict:
        """按 keyset 游标分批扫描 idle Scene，并限制单轮扫描总量。"""
        candidate_limit = max(int(limit), 1)
        max_scanned = max(int(scan_budget), 1)
        page_size = min(max(int(batch_size), 1), max_scanned)
        configs = {}
        candidates = []
        scanned = 0
        cursor_created_at = after_created_at
        cursor_id = after_id
        exhausted = False

        while scanned < max_scanned and len(candidates) < candidate_limit:
            fetch_size = min(page_size, max_scanned - scanned)
            starts = self._latest_shifted_scene_start_batch(
                after_created_at=cursor_created_at,
                after_id=cursor_id,
                batch_size=fetch_size,
            )
            if not starts:
                exhausted = True
                break

            missing_config_ids = {
                start.end_user_id
                for start in starts
                if start.end_user_id not in configs
            }
            if missing_config_ids:
                configs.update(self._load_scene_configs(missing_config_ids))

            for start in starts:
                cursor_created_at = start.created_at
                cursor_id = start.id
                scanned += 1
                end_user_id = start.end_user_id
                config = configs[end_user_id]
                if config is None:
                    continue
                if start.last_message_id is None or start.last_message_at is None:
                    continue
                idle_seconds = (utcnow_naive() - start.last_message_at).total_seconds()
                if idle_seconds < config.scene_idle_timeout_seconds:
                    continue

                candidates.append({
                    "end_user_id": end_user_id,
                    "config_id": str(config.config_id),
                    "scene_start_message_id": str(start.id),
                    "idle_high_watermark_message_id": str(start.last_message_id),
                })
                if len(candidates) >= candidate_limit:
                    break

            if len(candidates) >= candidate_limit or scanned >= max_scanned:
                break
            if len(starts) < fetch_size:
                exhausted = True
                break

        next_cursor = None
        if cursor_created_at is not None and cursor_id is not None:
            next_cursor = {
                "created_at": cursor_created_at,
                "id": str(cursor_id),
            }
        return {
            "candidates": candidates,
            "scanned": scanned,
            "next_cursor": next_cursor,
            "exhausted": exhausted,
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
        "scene_boundary": message.scene_boundary,
        "scene_summary_claimed_at": (
            to_iso_z(message.scene_summary_claimed_at)
            if message.scene_summary_claimed_at else None
        ),
    }
