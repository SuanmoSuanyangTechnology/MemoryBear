"""
MemoryMessageRepository — memory_messages 表的数据访问层

职责：
- 封装 memory_messages 表的 CRUD 操作
- 提供 write_cursor 原子推进
- 提供批量写入能力
- 供 MemoryWriteDispatcher 共用
"""

import logging
import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.utils.datetime_utils import to_iso_z, utcnow_naive
from app.models.conversation_model import Conversation
from app.models.memory_message_model import MemoryMessage

logger = logging.getLogger(__name__)


class MemoryMessageRepository:
    """memory_messages 表的数据访问层。

    提供消息写入、查询、游标推进等操作，供各入口点（API Service、Agent、
    Workflow、Flush、MCP）复用。
    """

    def __init__(self, db: Session):
        self.db = db

    # ──────────────────────────────────────────────
    # 消息写入
    # ──────────────────────────────────────────────

    def write_batch(
        self,
        conversation_id: str,
        messages: List[dict],
    ) -> List[dict]:
        """批量写入 memory_messages 表，自动分配递增 message_seq。

        在单个 DB 事务中完成：查询 max(message_seq) → 逐条分配 + 写入。
        调用方需自行 commit。

        Args:
            conversation_id: 对话 ID
            messages: 消息列表，每条格式 {"role": "user"|"assistant", "content": "...", "files": [...]}

        Returns:
            成功写入的消息摘要列表 [{"role": "user", "message_seq": 1, "content": "..."}, ...]
            （跳过 content 为空的消息）
        """
        written: List[dict] = []

        max_seq_result = self.db.execute(
            select(func.coalesce(func.max(MemoryMessage.message_seq), 0))
            .where(MemoryMessage.conversation_id == uuid.UUID(conversation_id))
        ).scalar()
        next_seq = max_seq_result or 0

        for msg in messages:
            role = str(msg.get("role", "user"))
            content = str(msg.get("content", "") or "")
            if not content.strip():
                continue

            next_seq += 1
            mm = MemoryMessage(
                id=uuid.uuid4(),
                conversation_id=uuid.UUID(conversation_id),
                original_message_id=None,
                role=role,
                content=content,
                message_seq=next_seq,
                should_memorize=msg.get("should_memorize", True),
                created_at=utcnow_naive(),
                dialog_at=msg.get("dialog_at") or None,
                files=msg.get("files"),
            )
            self.db.add(mm)
            written.append({
                "role": role,
                "message_seq": next_seq,
                "content": content,
                "dialog_at": mm.dialog_at,
            })
            logger.debug(
                f"[MemoryMessageRepository] 写入 memory_messages: "
                f"conv={conversation_id}, seq={next_seq}, role={role}"
            )

        return written

    def write_single(
        self,
        conversation_id: str,
        original_message_id: Optional[uuid.UUID],
        role: str,
        content: str,
        created_at: datetime,
        should_memorize: bool = True,
        files: Optional[list] = None,
        dialog_at: Optional[str] = None,
    ) -> Optional[MemoryMessage]:
        """写入单条消息到 memory_messages 表。

        自动分配递增 message_seq。调用方需自行 commit。

        Args:
            conversation_id: 会话 ID（字符串）
            original_message_id: 原始 messages 表行的 id（用于反查源消息）
            role: user/assistant/system
            content: 消息内容
            created_at: 时间戳
            should_memorize: 是否触发 Write_Pipeline
            files: 多模态文件信息列表
            dialog_at: 对话发生时间 ISO 8601 格式

        Returns:
            写入成功的 MemoryMessage 实例；失败时返回 None
        """
        try:
            max_seq = self.db.execute(
                select(func.coalesce(func.max(MemoryMessage.message_seq), 0))
                .where(MemoryMessage.conversation_id == uuid.UUID(conversation_id))
            ).scalar()
            next_seq = (max_seq or 0) + 1

            memory_msg = MemoryMessage(
                id=uuid.uuid4(),
                conversation_id=uuid.UUID(conversation_id),
                original_message_id=original_message_id,
                role=role,
                content=content,
                message_seq=next_seq,
                should_memorize=should_memorize,
                created_at=created_at,
                dialog_at=dialog_at,
                files=files,
            )
            self.db.add(memory_msg)
            logger.debug(
                f"[MemoryMessageRepository] 单条写入: "
                f"conv={conversation_id}, seq={next_seq}, role={role}"
            )
            return memory_msg
        except Exception as e:
            logger.error(
                f"[MemoryMessageRepository] 写入失败: "
                f"conv={conversation_id}, err={e}",
                exc_info=True,
            )
            return None

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
        """查询 message_seq > write_cursor 的待处理消息。

        Args:
            conversation_id: 对话 ID
            write_cursor: 当前游标位置
            role: 可选角色过滤（"user" / "assistant"）

        Returns:
            待处理消息列表，按 message_seq 升序排列
        """
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
    # write_cursor 操作
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

        Args:
            conversation_id: 对话 ID
            message_seq: 目标 message_seq

        Returns:
            是否成功推进（True 表示推进了，False 表示 cursor 已 >= seq）
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
    # 上下文窗口查询
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

        Returns:
            消息列表，按 message_seq 升序排列
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
        查询 (target_seq, lower_bound] 范围内所有消息。

        若下游无 user 消息，则返回 target_seq 之后的所有消息（包含尾部 assistant 消息），
        确保最后一条 assistant 消息也能作为上下文被处理。

        Returns:
            消息列表，按 message_seq 升序排列
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


def message_to_dict(message: MemoryMessage) -> dict:
    """将 MemoryMessage ORM 对象转换为字典格式。"""
    return {
        "role": message.role,
        "content": message.content,
        "message_seq": message.message_seq,
        "should_memorize": message.should_memorize,
        "created_at": to_iso_z(message.created_at) if message.created_at else None,
        "dialog_at": message.dialog_at,
        "files": message.files,
    }
