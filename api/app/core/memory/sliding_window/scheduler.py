"""
SlidingWindowScheduler — 滑动窗口写入调度器（条件检查 + 触发 Layer 2）

职责：
- 在 MemoryService 将消息写入 memory_messages 表后，检查下文条件
- 若任一待处理 user 消息的下文 ≥3 个 memorable Q，则派发到 celery_task_scheduler
  按 end_user_id 串行执行 execute_pending_from_pool()
- 不再在调用进程内同步 await Layer 2，统一通过 scheduler 入队保证 per-user 串行

Requirements: 1.1, 1.2
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select

from app.db import get_db_context
from app.models.conversation_model import Conversation
from app.models.memory_message_model import MemoryMessage

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
        3. should_memorize=FALSE 的消息先原子推进 write_cursor
        4. 若任一消息下游 ≥ WINDOW_SIZE 个 memorable user Q
           → 通过 celery_task_scheduler.push_task 按 end_user_id 串行派发候选池消费任务
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
        # 只推进 pending 头部连续的 false 消息——遇到第一个 true 消息就停，
        # 防止把后面尚未处理的 true 消息（如 seq=1）跳过。
        from app.core.memory.sliding_window.window_utils import advance_write_cursor

        for msg in pending:
            if msg.get("should_memorize", True):
                # 头部遇到第一个 true 消息，停止推进——它必须等到下文够 3 条
                # memorable Q 后由 execute_pending_from_pool 正常处理
                break
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
                self._enqueue_pending_consume(
                    conversation_id=conversation_id,
                    end_user_id=end_user_id,
                    config_id=config_id,
                    workspace_id=workspace_id,
                    language=language,
                )
                return

        logger.info(
            f"[SlidingWindowScheduler] 下文不足（< {WINDOW_SIZE} 条 memorable user Q），"
            f"等待更多消息: conv={conversation_id}, pending={len(pending)}"
        )

    @staticmethod
    def _enqueue_pending_consume(
        conversation_id: str,
        end_user_id: str,
        config_id: str,
        workspace_id: str,
        language: str,
    ) -> None:
        """通过 celery_task_scheduler 按 end_user_id 串行派发候选池消费任务。

        分片键 = end_user_id，与旧 write_message 任务沿用同一把 lock_key
        ("{task_name}:{end_user_id}")，保证同一 user 的所有写入串行。

        end_user_id 为空时回退到本进程同步执行（不阻塞主流程，但失去 per-user 串行保证）。
        """
        if not end_user_id:
            logger.warning(
                f"[SlidingWindowScheduler] end_user_id 为空，回退到同步执行: conv={conversation_id}"
            )
            try:
                import asyncio
                from app.core.memory.sliding_window.window_utils import execute_pending_from_pool

                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.ensure_future(execute_pending_from_pool(
                        conversation_id=conversation_id,
                        end_user_id=end_user_id,
                        config_id=config_id,
                        workspace_id=workspace_id,
                        language=language,
                    ))
                else:
                    loop.run_until_complete(execute_pending_from_pool(
                        conversation_id=conversation_id,
                        end_user_id=end_user_id,
                        config_id=config_id,
                        workspace_id=workspace_id,
                        language=language,
                    ))
            except Exception as e:
                logger.error(
                    f"[SlidingWindowScheduler] 同步执行 execute_pending_from_pool 失败: "
                    f"conv={conversation_id}, err={e}",
                    exc_info=True,
                )
            return

        try:
            from app.celery_task_scheduler import scheduler as celery_scheduler

            msg_id = celery_scheduler.push_task(
                "app.core.memory.agent.write_message",
                end_user_id,
                {
                    "end_user_id": end_user_id,
                    # 不传 message → write_message_task 走"仅消费候选池"模式
                    "message": [],
                    "config_id": config_id or "",
                    "storage_type": "neo4j",
                    "user_rag_memory_id": "",
                    "language": language,
                    "conversation_id": conversation_id,
                    "workspace_id": workspace_id or "",
                },
            )
            logger.info(
                f"[SlidingWindowScheduler] 已派发候选池消费任务: "
                f"conv={conversation_id}, end_user_id={end_user_id}, msg_id={msg_id}"
            )
        except Exception as e:
            logger.error(
                f"[SlidingWindowScheduler] push_task 失败（不影响主流程）: "
                f"conv={conversation_id}, end_user_id={end_user_id}, err={e}",
                exc_info=True,
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
        """统计 target_seq 之后的 user 消息数（窗口下文）。

        无论 should_memorize=true/false 都计入：should_memorize=false 的消息
        虽然不会触发 Neo4j 写入，但仍作为窗口上下文参与计数；写入流程会跳过
        这些消息并推进 write_cursor。
        """
        try:
            with get_db_context() as db:
                count = db.execute(
                    select(func.count(MemoryMessage.id)).where(
                        MemoryMessage.conversation_id == conversation_id,
                        MemoryMessage.role == "user",
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
