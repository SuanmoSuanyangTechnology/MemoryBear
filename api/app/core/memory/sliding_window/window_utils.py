"""
滑动窗口共享工具函数

scheduler.py 和 flush_task.py 共用的窗口上下文构建、write_cursor 推进
和 MemoryMessage 转换逻辑，避免 200+ 行重复代码。

数据源：所有查询均基于 memory_messages 表，仅按 conversation_id 维度过滤。
"""

from __future__ import annotations

import logging
from typing import List

from sqlalchemy import select, update

from app.db import get_db_context
from app.models.conversation_model import Conversation
from app.models.memory_message_model import MemoryMessage

logger = logging.getLogger(__name__)

WINDOW_SIZE = 3


# ──────────────────────────────────────────────
# 窗口上下文构建
# ──────────────────────────────────────────────


async def build_context_before(
    conversation_id: str,
    target_seq: int,
) -> List[dict]:
    """构建上文消息列表（从 memory_messages 表）。

    向前查找最多 WINDOW_SIZE 个 user 消息，取最小 message_seq 作为上边界，
    查询 [upper_bound, target_seq) 范围内所有消息（含穿插的 A），
    按 message_seq 升序排列。
    """
    try:
        with get_db_context() as db:
            upstream_q_seqs = (
                db.execute(
                    select(MemoryMessage.message_seq)
                    .where(
                        MemoryMessage.conversation_id == conversation_id,
                        MemoryMessage.role == "user",
                        MemoryMessage.message_seq < target_seq,
                    )
                    .order_by(MemoryMessage.message_seq.desc())
                    .limit(WINDOW_SIZE)
                )
                .scalars()
                .all()
            )

            if not upstream_q_seqs:
                return []

            upper_bound = min(upstream_q_seqs)

            messages = (
                db.execute(
                    select(MemoryMessage)
                    .where(
                        MemoryMessage.conversation_id == conversation_id,
                        MemoryMessage.message_seq >= upper_bound,
                        MemoryMessage.message_seq < target_seq,
                    )
                    .order_by(MemoryMessage.message_seq.asc())
                )
                .scalars()
                .all()
            )

            return [message_to_dict(msg) for msg in messages]
    except Exception as e:
        logger.error(
            f"[WindowUtils] 构建上文失败: "
            f"conv={conversation_id}, target_seq={target_seq}, err={e}",
            exc_info=True,
        )
        return []


async def build_context_after(
    conversation_id: str,
    target_seq: int,
) -> List[dict]:
    """构建下文消息列表（从 memory_messages 表）。

    向后查找最多 WINDOW_SIZE 个 user 消息，取最大 message_seq 作为下边界，
    查询 (target_seq, lower_bound] 范围内所有消息（含穿插的 A），
    按 message_seq 升序排列。
    """
    try:
        with get_db_context() as db:
            downstream_q_seqs = (
                db.execute(
                    select(MemoryMessage.message_seq)
                    .where(
                        MemoryMessage.conversation_id == conversation_id,
                        MemoryMessage.role == "user",
                        MemoryMessage.message_seq > target_seq,
                    )
                    .order_by(MemoryMessage.message_seq.asc())
                    .limit(WINDOW_SIZE)
                )
                .scalars()
                .all()
            )

            if not downstream_q_seqs:
                return []

            lower_bound = max(downstream_q_seqs)

            messages = (
                db.execute(
                    select(MemoryMessage)
                    .where(
                        MemoryMessage.conversation_id == conversation_id,
                        MemoryMessage.message_seq > target_seq,
                        MemoryMessage.message_seq <= lower_bound,
                    )
                    .order_by(MemoryMessage.message_seq.asc())
                )
                .scalars()
                .all()
            )

            return [message_to_dict(msg) for msg in messages]
    except Exception as e:
        logger.error(
            f"[WindowUtils] 构建下文失败: "
            f"conv={conversation_id}, target_seq={target_seq}, err={e}",
            exc_info=True,
        )
        return []


# ──────────────────────────────────────────────
# write_cursor 原子推进
# ──────────────────────────────────────────────


async def advance_write_cursor(
    conversation_id: str,
    message_seq: int,
) -> None:
    """原子推进 write_cursor。

    UPDATE conversations SET write_cursor = :seq
    WHERE id = :conv_id AND write_cursor < :seq，
    确保 write_cursor 只能单调递增。
    """
    try:
        with get_db_context() as db:
            db.execute(
                update(Conversation)
                .where(
                    Conversation.id == conversation_id,
                    Conversation.write_cursor < message_seq,
                )
                .values(write_cursor=message_seq)
            )
            db.commit()
            logger.debug(
                f"[WindowUtils] write_cursor 已推进: conv={conversation_id}, seq={message_seq}"
            )
    except Exception as e:
        logger.warning(
            f"[WindowUtils] 推进 write_cursor 失败: "
            f"conv={conversation_id}, seq={message_seq}, err={e}",
            exc_info=True,
        )


# ──────────────────────────────────────────────
# 辅助转换
# ──────────────────────────────────────────────


def message_to_dict(message: MemoryMessage) -> dict:
    """将 MemoryMessage ORM 对象转换为字典格式。"""
    return {
        "role": message.role,
        "content": message.content,
        "message_seq": message.message_seq,
        "should_memorize": message.should_memorize,
        "created_at": (
            message.created_at.isoformat()
            if message.created_at is not None
            else None
        ),
    }
