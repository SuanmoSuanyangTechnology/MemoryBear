"""
SlidingWindowScheduler — 滑动窗口写入调度器（条件检查 + 触发 Layer 2）

职责：
- 在 MemoryService 将消息写入 memory_messages 表后，检查下文条件
- 若任一待处理 user 消息的下文 ≥3 个 memorable Q，则调用 execute_pending_from_pool()
- 不再自行派发 Celery 任务——统一委托给 Layer 2 执行

Requirements: 1.1, 1.2
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select

from app.db import get_db_context
from app.models.conversation_model import Conversation
from app.models.memory_message_model import MemoryMessage

from app.core.memory.sliding_window.window_utils import execute_pending_from_pool

logger = logging.getLogger(__name__)

WINDOW_SIZE = 3


class SlidingWindowScheduler:
    """滑动窗口写入调度器。

    只负责条件检查：在消息写入 memory_messages 后，判断是否有 user 消息
    满足下文条件（≥3 条 memorable user Q），若满足则触发 execute_pending_from_pool()。

    数据源：所有查询均基于 memory_messages 表。
    """

    async def check_and_dispatch(
        self,
        conversation_id: str,
        config_id: str = "",
        end_user_id: str = "",
        workspace_id: str = "",
        language: str = "zh",
    ) -> None:
        """MemoryService 写入 memory_messages 后的调度入口。

        1. 查询 write_cursor
        2. 查询 message_seq > write_cursor 的 user 消息
        3. 若任一消息下游 ≥ WINDOW_SIZE 个 memorable user Q → 调用 execute_pending_from_pool()
        4. should_memorize=FALSE 的消息先原子推进 write_cursor
        """
        if not conversation_id:
            logger.warning("[SlidingWindowScheduler] conversation_id 为空，跳过")
            return

        # Step 1: 查询 write_cursor
        write_cursor = self._get_write_cursor(conversation_id)
        if write_cursor is None:
            logger.warning(
                f"[SlidingWindowScheduler] 对话不存在或无 write_cursor: conv={conversation_id}"
            )
            return

        # Step 2: 查询待处理的 user 消息
        pending = self._get_pending_user_messages(conversation_id, write_cursor)
        if not pending:
            logger.debug(
                f"[SlidingWindowScheduler] 无待处理 user 消息: conv={conversation_id}, "
                f"write_cursor={write_cursor}"
            )
            return

        logger.info(
            f"[SlidingWindowScheduler] 待处理 user 消息: {len(pending)}, "
            f"conv={conversation_id}, write_cursor={write_cursor}"
        )

        # Step 3: 推进 should_memorize=FALSE 的游标
        from app.core.memory.sliding_window.window_utils import advance_write_cursor

        for msg in pending:
            if not msg.get("should_memorize", True):
                target_seq = msg.get("message_seq")
                if target_seq is not None:
                    await advance_write_cursor(conversation_id, target_seq)

        # Step 4: 检查是否有满足下文条件的消息
        for msg in pending:
            if not msg.get("should_memorize", True):
                continue
            target_seq = msg.get("message_seq")
            if target_seq is None:
                continue

            downstream_count = self._count_downstream_memorable_user_messages(
                conversation_id, target_seq
            )
            if downstream_count >= WINDOW_SIZE:
                logger.info(
                    f"[SlidingWindowScheduler] 满足写入条件: "
                    f"conv={conversation_id}, seq={target_seq}, downstream={downstream_count}"
                )
                await execute_pending_from_pool(
                    conversation_id=conversation_id,
                    end_user_id=end_user_id,
                    config_id=config_id,
                    workspace_id=workspace_id,
                    language=language,
                )
                return

        logger.debug(
            f"[SlidingWindowScheduler] 下文不足，等待更多消息: conv={conversation_id}"
        )

    # ──────────────────────────────────────────────
    # 内部方法
    # ──────────────────────────────────────────────

    @staticmethod
    def _get_write_cursor(conversation_id: str) -> int | None:
        try:
            with get_db_context() as db:
                return db.execute(
                    select(Conversation.write_cursor).where(
                        Conversation.id == conversation_id
                    )
                ).scalar_one_or_none()
        except Exception as e:
            logger.error(
                f"[SlidingWindowScheduler] 查询 write_cursor 失败: "
                f"conv={conversation_id}, err={e}",
                exc_info=True,
            )
            return None

    @staticmethod
    def _get_pending_user_messages(
        conversation_id: str, write_cursor: int
    ) -> list[dict]:
        try:
            with get_db_context() as db:
                from app.core.memory.sliding_window.window_utils import message_to_dict

                messages = (
                    db.execute(
                        select(MemoryMessage)
                        .where(
                            MemoryMessage.conversation_id == conversation_id,
                            MemoryMessage.message_seq > write_cursor,
                            MemoryMessage.role == "user",
                        )
                        .order_by(MemoryMessage.message_seq.asc())
                    )
                    .scalars()
                    .all()
                )
                return [message_to_dict(msg) for msg in messages]
        except Exception as e:
            logger.error(
                f"[SlidingWindowScheduler] 查询待处理消息失败: "
                f"conv={conversation_id}, err={e}",
                exc_info=True,
            )
            return []

    @staticmethod
    def _count_downstream_memorable_user_messages(
        conversation_id: str,
        target_seq: int,
    ) -> int:
        try:
            with get_db_context() as db:
                count = db.execute(
                    select(func.count(MemoryMessage.id)).where(
                        MemoryMessage.conversation_id == conversation_id,
                        MemoryMessage.role == "user",
                        MemoryMessage.should_memorize.is_(True),
                        MemoryMessage.message_seq > target_seq,
                    )
                ).scalar_one()
                return count or 0
        except Exception as e:
            logger.error(
                f"[SlidingWindowScheduler] 统计下文消息数失败: "
                f"conv={conversation_id}, target_seq={target_seq}, err={e}",
                exc_info=True,
            )
            return 0
