"""
SlidingWindowScheduler — 滑动窗口写入调度器

职责：
- 在 MemoryService 将消息写入 memory_messages 表后，检查写入条件并派发 Celery 写入任务
- 查询 memory_messages 表中待写入消息（message_seq > write_cursor），仅按 conversation_id 维度
- 对 should_memorize=FALSE 的消息原子推进 write_cursor 并跳过
- 对满足"下文 ≥3 个 should_memorize=TRUE 的 Q"条件的消息构建 WindowContext 并派发写入任务
- 幂等派发：通过 write_task:{conversation_id}:{message_seq} 防止重复派发

Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.7, 1.8
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional, TypedDict

from sqlalchemy import select

from app.db import get_db_context
from app.models.conversation_model import Conversation
from app.models.memory_message_model import MemoryMessage

from app.core.memory.sliding_window.window_utils import (
    build_context_before,
    build_context_after,
    advance_write_cursor,
    message_to_dict,
)

logger = logging.getLogger(__name__)


class WindowContext(TypedDict):
    """传入 Celery 任务的窗口上下文参数结构。"""

    conversation_id: str
    target_message_seq: int
    context_before: List[dict]
    context_after: List[dict]
    dispatch_at: str


class SlidingWindowScheduler:
    """滑动窗口写入调度器。

    在 MemoryService 将消息写入 memory_messages 表后检查写入条件，
    对满足条件的 user 消息派发异步 Celery 写入任务。

    窗口规格：以 user 消息（Q）为计数单位，3 上 3 下。
    触发条件：目标 Q 之后已存在 ≥3 条 should_memorize=TRUE 的 user 消息时触发写入。
    数据源：所有查询均基于 memory_messages 表，仅按 conversation_id 维度过滤。
    """

    WINDOW_SIZE = 3  # 上下文各取 3 个 Q

    # ──────────────────────────────────────────────
    # 公开接口
    # ──────────────────────────────────────────────

    async def check_and_dispatch(
        self,
        conversation_id: str,
        config_id: str = "",
        end_user_id: str = "",
        workspace_id: str = "",
        language: str = "zh",
    ) -> None:
        """MemoryService 写入 memory_messages 后的调度入口。

        流程：
        1. 查询对话的 write_cursor
        2. 查询 memory_messages 中 message_seq > write_cursor 的消息
        3. should_memorize=FALSE → 原子推进 write_cursor，跳过
        4. should_memorize=TRUE + role=user → 检查下文条件，满足则派发写入任务
        5. should_memorize=TRUE + role=assistant → 跳过（在 Write_Pipeline 的 Pruned_Context 阶段处理）

        Args:
            conversation_id: 对话 ID
            config_id: 记忆配置 ID
            end_user_id: 终端用户 ID
            workspace_id: 工作空间 ID
            language: 语言（"zh" | "en"）

        Requirements: 1.1, 1.2, 1.8
        """
        if not conversation_id:
            logger.warning("[SlidingWindowScheduler] conversation_id 为空，跳过")
            return

        # Step 1: 查询 write_cursor
        write_cursor = await self._get_write_cursor(conversation_id=conversation_id)
        if write_cursor is None:
            logger.warning(
                f"[SlidingWindowScheduler] 对话不存在或无 write_cursor: conv={conversation_id}"
            )
            return

        # Step 2: 查询 memory_messages 中待处理消息（含 should_memorize 两个值）
        pending_messages = await self._get_pending_messages(
            conversation_id=conversation_id,
            write_cursor=write_cursor,
        )

        if not pending_messages:
            logger.debug(
                f"[SlidingWindowScheduler] 无待处理消息: conv={conversation_id}, "
                f"write_cursor={write_cursor}"
            )
            return

        logger.info(
            f"[SlidingWindowScheduler] 待处理消息数: {len(pending_messages)}, "
            f"conv={conversation_id}, write_cursor={write_cursor}"
        )

        # Step 3 & 4: 逐条处理
        for message in pending_messages:
            target_seq = message.get("message_seq")
            if target_seq is None:
                logger.warning(
                    f"[SlidingWindowScheduler] 消息 message_seq 为空，跳过: "
                    f"conv={conversation_id}, msg={message}"
                )
                continue

            # should_memorize=FALSE → 原子推进 write_cursor，跳过写入
            if not message.get("should_memorize", True):
                logger.info(
                    f"[SlidingWindowScheduler] should_memorize=FALSE，跳过并推进 cursor: "
                    f"conv={conversation_id}, seq={target_seq}"
                )
                await advance_write_cursor(
                    conversation_id=conversation_id,
                    message_seq=target_seq,
                )
                continue

            # should_memorize=TRUE 且 role != user → 跳过，不推进 cursor
            # assistant 消息由 FlushTask 兜底处理（Write_Pipeline 的 Pruned_Context 阶段），
            # 不应推进 cursor，否则会越过前面未处理的 user 消息导致其永久丢失
            if message.get("role") != "user":
                logger.debug(
                    f"[SlidingWindowScheduler] 非 user 消息，跳过（不推进 cursor）: "
                    f"conv={conversation_id}, seq={target_seq}, role={message.get('role')}"
                )
                continue

            # 检查下文中 should_memorize=TRUE 的 user 消息数量
            downstream_count = await self._count_downstream_memorable_user_messages(
                conversation_id=conversation_id,
                target_seq=target_seq,
            )

            if downstream_count >= self.WINDOW_SIZE:
                logger.info(
                    f"[SlidingWindowScheduler] 满足写入条件: "
                    f"conv={conversation_id}, seq={target_seq}, "
                    f"downstream_count={downstream_count}"
                )
                window_context = await self._build_window_context(
                    conversation_id=conversation_id,
                    target_seq=target_seq,
                )
                await self._dispatch_write_task(
                    conversation_id=conversation_id,
                    message_seq=target_seq,
                    window_context=window_context,
                    config_id=config_id,
                    end_user_id=end_user_id,
                    workspace_id=workspace_id,
                    language=language,
                )
            else:
                logger.debug(
                    f"[SlidingWindowScheduler] 下文不足，等待更多消息: "
                    f"conv={conversation_id}, seq={target_seq}, "
                    f"downstream_count={downstream_count}"
                )

    # ──────────────────────────────────────────────
    # 内部方法：数据库查询
    # ──────────────────────────────────────────────

    async def _get_write_cursor(self, conversation_id: str) -> Optional[int]:
        """查询对话的 write_cursor。

        Args:
            conversation_id: 对话 ID

        Returns:
            write_cursor 值；对话不存在时返回 None
        """
        try:
            with get_db_context() as db:
                result = db.execute(
                    select(Conversation.write_cursor).where(
                        Conversation.id == conversation_id
                    )
                ).scalar_one_or_none()
                return result
        except Exception as e:
            logger.error(
                f"[SlidingWindowScheduler] 查询 write_cursor 失败: "
                f"conv={conversation_id}, err={e}",
                exc_info=True,
            )
            return None

    async def _get_pending_messages(
        self,
        conversation_id: str,
        write_cursor: int,
    ) -> List[dict]:
        """查询 memory_messages 表中待处理的消息（含 should_memorize 两个值）。

        返回字典列表而非 ORM 对象，避免 session 关闭后 detached 访问问题。

        Args:
            conversation_id: 对话 ID
            write_cursor: 当前写入游标

        Returns:
            待处理消息字典列表，按 message_seq 升序

        Requirements: 1.1
        """
        try:
            with get_db_context() as db:
                query = (
                    select(MemoryMessage)
                    .where(
                        MemoryMessage.conversation_id == conversation_id,
                        MemoryMessage.message_seq > write_cursor,
                    )
                    .order_by(MemoryMessage.message_seq.asc())
                )
                messages = db.execute(query).scalars().all()
                # 在 session 关闭前转成字典，避免 detached instance 错误
                return [message_to_dict(msg) for msg in messages]
        except Exception as e:
            logger.error(
                f"[SlidingWindowScheduler] 查询待处理消息失败: "
                f"conv={conversation_id}, write_cursor={write_cursor}, err={e}",
                exc_info=True,
            )
            return []

    async def _count_downstream_memorable_user_messages(
        self,
        conversation_id: str,
        target_seq: int,
    ) -> int:
        """统计目标消息之后 should_memorize=TRUE 的 user 消息数量。

        Args:
            conversation_id: 对话 ID
            target_seq: 目标消息的 message_seq

        Returns:
            下文中 should_memorize=TRUE 的 user 消息数量

        Requirements: 1.1, 1.2
        """
        try:
            from sqlalchemy import func

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

    # ──────────────────────────────────────────────
    # 内部方法：窗口上下文构建
    # ──────────────────────────────────────────────

    async def _build_window_context(
        self,
        conversation_id: str,
        target_seq: int,
    ) -> WindowContext:
        """构建目标消息的窗口上下文（从 memory_messages 表读取）。

        Requirements: 1.3, 1.4
        """
        context_before_list = await build_context_before(
            conversation_id=conversation_id,
            target_seq=target_seq,
        )
        context_after_list = await build_context_after(
            conversation_id=conversation_id,
            target_seq=target_seq,
        )

        dispatch_at = datetime.now(timezone.utc).isoformat()

        return WindowContext(
            conversation_id=conversation_id,
            target_message_seq=target_seq,
            context_before=context_before_list,
            context_after=context_after_list,
            dispatch_at=dispatch_at,
        )

    # ──────────────────────────────────────────────
    # 内部方法：任务派发
    # ──────────────────────────────────────────────

    async def _dispatch_write_task(
        self,
        conversation_id: str,
        message_seq: int,
        window_context: WindowContext,
        config_id: str,
        end_user_id: str,
        workspace_id: str,
        language: str,
    ) -> None:
        """幂等派发 Celery 写入任务。

        幂等 key 格式：write_task:{conversation_id}:{message_seq}

        Requirements: 1.2, 1.5, 1.7
        """
        idempotency_key = f"write_task:{conversation_id}:{message_seq}"

        try:
            from app.aioRedis import get_thread_safe_redis

            redis_client = get_thread_safe_redis()
            # nx=True 防并发，ex=3600 防进程崩溃后锁泄漏
            acquired = await redis_client.set(idempotency_key, "1", nx=True, ex=3600)

            if not acquired:
                logger.info(
                    f"[SlidingWindowScheduler] 幂等锁已存在，跳过派发: key={idempotency_key}"
                )
                return

            logger.info(
                f"[SlidingWindowScheduler] 幂等锁已设置，准备派发任务: key={idempotency_key}"
            )
        except Exception as e:
            logger.error(
                f"[SlidingWindowScheduler] Redis 幂等锁操作失败: "
                f"key={idempotency_key}, err={e}",
                exc_info=True,
            )
            logger.warning(
                f"[SlidingWindowScheduler] Redis 不可用，降级为直接派发（可能重复）: "
                f"conv={conversation_id}, seq={message_seq}"
            )

        try:
            from app.tasks import sliding_window_write_task

            target_message = await self._get_target_message(
                conversation_id=conversation_id,
                message_seq=message_seq,
            )
            if target_message is None:
                logger.error(
                    f"[SlidingWindowScheduler] 目标消息不存在，取消派发: "
                    f"conv={conversation_id}, seq={message_seq}"
                )
                try:
                    from app.aioRedis import get_thread_safe_redis as _get_redis
                    await _get_redis().delete(idempotency_key)
                except Exception:
                    pass
                return

            sliding_window_write_task.apply_async(
                kwargs={
                    "conversation_id": conversation_id,
                    "message_seq": message_seq,
                    "context_before": window_context["context_before"],
                    "context_after": window_context["context_after"],
                    "target_message": target_message,
                    "config_id": config_id,
                    "end_user_id": end_user_id,
                    "workspace_id": workspace_id,
                    "language": language,
                    "dispatch_at": window_context["dispatch_at"],
                },
                queue="memory_tasks",
            )

            logger.info(
                f"[SlidingWindowScheduler] 写入任务已派发: "
                f"conv={conversation_id}, seq={message_seq}, "
                f"dispatch_at={window_context['dispatch_at']}"
            )
        except Exception as e:
            logger.error(
                f"[SlidingWindowScheduler] 派发写入任务失败: "
                f"conv={conversation_id}, seq={message_seq}, err={e}",
                exc_info=True,
            )
            try:
                from app.aioRedis import get_thread_safe_redis as _get_redis
                await _get_redis().delete(idempotency_key)
            except Exception as redis_err:
                logger.warning(
                    f"[SlidingWindowScheduler] 清理幂等锁失败: "
                    f"key={idempotency_key}, err={redis_err}"
                )

    # ──────────────────────────────────────────────
    # 辅助方法
    # ──────────────────────────────────────────────

    async def _get_target_message(
        self,
        conversation_id: str,
        message_seq: int,
    ) -> Optional[dict]:
        """从 memory_messages 表查询目标消息并转换为字典格式。"""
        try:
            with get_db_context() as db:
                message = db.execute(
                    select(MemoryMessage).where(
                        MemoryMessage.conversation_id == conversation_id,
                        MemoryMessage.message_seq == message_seq,
                    )
                ).scalar_one_or_none()

                if message is None:
                    return None

                return message_to_dict(message)
        except Exception as e:
            logger.error(
                f"[SlidingWindowScheduler] 查询目标消息失败: "
                f"conv={conversation_id}, seq={message_seq}, err={e}",
                exc_info=True,
            )
            return None

