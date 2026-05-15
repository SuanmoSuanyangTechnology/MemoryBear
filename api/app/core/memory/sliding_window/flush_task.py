"""
FlushTask — 兜底写入任务

职责：
- 逐条处理 write_cursor 之后的所有未写入消息（不依赖下文条件）
- should_memorize=FALSE → 跳过写入，原子推进 write_cursor
- role=user 且 should_memorize=TRUE → 完整 WritePipeline.run_with_window()（含上下文剪枝 + 知识萃取），成功后原子推进 write_cursor
- role=assistant 且 should_memorize=TRUE → 单独调用 PruningPipeline.prune()，完成后原子推进 write_cursor
- 某条消息处理异常时：记录日志，跳过该条，继续处理后续消息，不回滚已写入消息

数据源：所有查询均基于 memory_messages 表（v2 架构）

窗口构建算法（_build_flush_window_context）：
- 上文：向上找最多 3 个 Q（不足时取全部，第一条时为空列表），按 message_seq 升序排列
- 下文：向下找最多 3 个 Q（不足时取全部，无下文时为空列表），按 message_seq 升序排列
- 上下文中穿插的 A 均包含在内，但不计入 Q 的计数

Requirements: 4.3, 4.4, 4.5
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

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


class FlushTask:
    """兜底写入任务。

    逐条处理对话中 write_cursor 之后的所有未写入消息，不依赖下文条件。
    由 Celery Beat 定时扫描触发，处理因下文不足而未被 SlidingWindowScheduler 处理的消息。
    """

    # ──────────────────────────────────────────────
    # 公开接口
    # ──────────────────────────────────────────────

    async def run(self, conversation_id: str) -> None:
        """逐条处理 write_cursor 之后的所有未写入消息。

        流程：
        1. 查询对话的 write_cursor 和 end_user_id
        2. 加载 memory_config
        3. 查询 memory_messages 表中 message_seq > write_cursor 的所有消息，按 message_seq 升序
        4. 对每条消息：
           - should_memorize=FALSE → 跳过写入，原子推进 write_cursor
           - role=user 且 should_memorize=TRUE → 构建窗口上下文，调用 WritePipeline.run_with_window()，成功后推进 write_cursor
           - role=assistant 且 should_memorize=TRUE → 调用 PruningPipeline.prune()，完成后推进 write_cursor
           - 异常 → 记录日志，跳过该条，继续处理后续消息

        Args:
            conversation_id: 对话 ID

        Requirements: 4.3, 4.5
        """
        logger.info(f"[FlushTask] 开始处理: conv={conversation_id}")

        # Step 1: 查询对话信息（write_cursor + end_user_id + workspace_id）
        conversation_info = await self._get_conversation_info(conversation_id)
        if conversation_info is None:
            logger.error(f"[FlushTask] 对话不存在: conv={conversation_id}")
            return

        write_cursor, end_user_id, workspace_id = conversation_info

        # Step 2: 加载 memory_config（优先用 workspace_id，回退到 end_user_id）
        memory_config = await self._load_memory_config(
            end_user_id=end_user_id,
            workspace_id=workspace_id,
        )
        if memory_config is None:
            logger.error(
                f"[FlushTask] 无法加载 memory_config: "
                f"conv={conversation_id}, end_user_id={end_user_id}, workspace_id={workspace_id}"
            )
            return

        language = str(getattr(memory_config, "language", "zh") or "zh")

        # Step 3: 查询 memory_messages 表中所有待处理消息（message_seq > write_cursor，按升序）
        pending_messages = await self._get_pending_messages(conversation_id, write_cursor)

        if not pending_messages:
            logger.info(
                f"[FlushTask] 无待处理消息: conv={conversation_id}, write_cursor={write_cursor}"
            )
            return

        logger.info(
            f"[FlushTask] 待处理消息数: {len(pending_messages)}, "
            f"conv={conversation_id}, write_cursor={write_cursor}"
        )

        # Step 4: 逐条处理
        from app.core.memory.pipelines.pruning_pipeline import PruningPipeline
        from app.core.memory.pipelines.write_pipeline import WritePipeline

        pruning_pipeline = PruningPipeline(
            memory_config=memory_config,
            end_user_id=end_user_id,
            language=language,
        )

        for message in pending_messages:
            target_seq = message.message_seq
            if target_seq is None:
                logger.warning(
                    f"[FlushTask] 消息 message_seq 为空，跳过: "
                    f"conv={conversation_id}, msg_id={message.id}"
                )
                continue

            try:
                # should_memorize=FALSE → 跳过写入，原子推进 write_cursor（Requirements 4.3）
                if not message.should_memorize:
                    logger.info(
                        f"[FlushTask] should_memorize=FALSE，跳过并推进 cursor: "
                        f"conv={conversation_id}, seq={target_seq}"
                    )
                    await advance_write_cursor(conversation_id, target_seq)
                    continue

                if message.role == "user":
                    # 构建窗口上下文（从 memory_messages 表）
                    context_before, context_after = await self._build_flush_window_context(
                        conversation_id=conversation_id,
                        target_seq=target_seq,
                    )

                    target_message = message_to_dict(message)

                    # 调用完整 WritePipeline（含上下文剪枝 + 知识萃取）
                    write_pipeline = WritePipeline(
                        memory_config=memory_config,
                        end_user_id=end_user_id,
                        language=language,
                    )
                    await write_pipeline.run_with_window(
                        target_message=target_message,
                        context_before=context_before,
                        context_after=context_after,
                        conversation_id=conversation_id,
                        message_seq=target_seq,
                    )
                    # write_cursor 由 WritePipeline.run_with_window() 内部原子推进
                    logger.info(
                        f"[FlushTask] user 消息处理完成: "
                        f"conv={conversation_id}, seq={target_seq}"
                    )

                elif message.role == "assistant":
                    # 调用 PruningPipeline，完成后原子推进 write_cursor（Requirements 4.3）
                    await pruning_pipeline.prune(
                        conversation_id=conversation_id,
                        message_seq=target_seq,
                        content=message.content or "",
                    )
                    # assistant 消息剪枝完成后也推进 write_cursor（v2 新增）
                    await advance_write_cursor(conversation_id, target_seq)
                    logger.info(
                        f"[FlushTask] assistant 消息剪枝完成: "
                        f"conv={conversation_id}, seq={target_seq}"
                    )

                else:
                    logger.warning(
                        f"[FlushTask] 未知 role，跳过: "
                        f"conv={conversation_id}, seq={target_seq}, role={message.role}"
                    )

            except Exception as e:
                logger.error(
                    f"[FlushTask] 消息处理异常，跳过: "
                    f"conv={conversation_id}, seq={target_seq}, err={e}",
                    exc_info=True,
                )
                continue

        logger.info(f"[FlushTask] 处理完成: conv={conversation_id}")

    # ──────────────────────────────────────────────
    # 窗口上下文构建
    # ──────────────────────────────────────────────

    async def _build_flush_window_context(
        self,
        conversation_id: str,
        target_seq: int,
    ) -> Tuple[List[dict], List[dict]]:
        """为 Flush 场景构建窗口上下文（不依赖下文条件）。

        上文算法：
        - 向前查找最多 WINDOW_SIZE 个 role='user' 消息的 message_seq，取最小值作为上边界
        - 查询 [upper_bound, target_seq) 范围内所有消息（含穿插的 A），按 message_seq 升序
        - 不足 WINDOW_SIZE 个时取全部上文，无上文时返回空列表

        下文算法：
        - 向后查找最多 WINDOW_SIZE 个 role='user' 消息的 message_seq，取最大值作为下边界
        - 查询 (target_seq, lower_bound] 范围内所有消息（含穿插的 A），按 message_seq 升序
        - 不足 WINDOW_SIZE 个时取全部下文，无下文时返回空列表

        上下文中穿插的 A 均包含在内，但不计入 Q 的计数。

        Args:
            conversation_id: 对话 ID
            target_seq: 目标消息的 message_seq

        Returns:
            (context_before, context_after) 元组，均按 message_seq 升序排列

        Requirements: 4.4
        """
        context_before = await build_context_before(conversation_id, target_seq)
        context_after = await build_context_after(conversation_id, target_seq)
        return context_before, context_after

    # ──────────────────────────────────────────────
    # 内部方法：数据库查询
    # ──────────────────────────────────────────────

    async def _get_conversation_info(
        self, conversation_id: str
    ) -> Tuple[int, str | None, str | None] | None:
        """查询对话的 write_cursor、user_id 和 workspace_id。

        Args:
            conversation_id: 对话 ID

        Returns:
            (write_cursor, user_id, workspace_id) 元组，对话不存在时返回 None
        """
        try:
            with get_db_context() as db:
                result = db.execute(
                    select(
                        Conversation.write_cursor,
                        Conversation.user_id,
                        Conversation.workspace_id,
                    ).where(
                        Conversation.id == conversation_id
                    )
                ).one_or_none()

                if result is None:
                    return None

                write_cursor, user_id, workspace_id = result
                return write_cursor, user_id, str(workspace_id) if workspace_id else None
        except Exception as e:
            logger.error(
                f"[FlushTask] 查询对话信息失败: conv={conversation_id}, err={e}",
                exc_info=True,
            )
            return None

    async def _get_pending_messages(
        self, conversation_id: str, write_cursor: int
    ) -> List[MemoryMessage]:
        """查询 memory_messages 表中 write_cursor 之后的所有未写入消息。

        查询 message_seq > write_cursor 的所有消息（包含 user 和 assistant），
        按 message_seq 升序排列。

        Requirements: 4.3
        """
        try:
            with get_db_context() as db:
                messages = (
                    db.execute(
                        select(MemoryMessage)
                        .where(
                            MemoryMessage.conversation_id == conversation_id,
                            MemoryMessage.message_seq > write_cursor,
                        )
                        .order_by(MemoryMessage.message_seq.asc())
                    )
                    .scalars()
                    .all()
                )
                return list(messages)
        except Exception as e:
            logger.error(
                f"[FlushTask] 查询待处理消息失败: "
                f"conv={conversation_id}, write_cursor={write_cursor}, err={e}",
                exc_info=True,
            )
            return []

    async def _load_memory_config(
        self,
        end_user_id: str | None = None,
        workspace_id: str | None = None,
    ):
        """加载 memory_config。

        优先通过 workspace_id 直接加载（workspace 默认配置）。
        若 workspace_id 为空，回退到通过 end_user_id 查关联配置。

        Args:
            end_user_id: 终端用户 ID（回退路径）
            workspace_id: 工作空间 ID（优先路径）

        Returns:
            MemoryConfig 对象，加载失败时返回 None
        """
        try:
            from app.services.memory_config_service import MemoryConfigService
            import uuid as _uuid

            # 优先路径：直接用 workspace_id 加载 workspace 默认配置
            if workspace_id:
                try:
                    ws_uuid = _uuid.UUID(str(workspace_id))
                    with get_db_context() as db:
                        memory_config = MemoryConfigService(db).load_memory_config(
                            config_id=None,
                            workspace_id=ws_uuid,
                            service_name="FlushTask",
                        )
                    return memory_config
                except Exception as e:
                    logger.warning(
                        f"[FlushTask] 通过 workspace_id 加载配置失败，尝试 end_user_id 回退: "
                        f"workspace_id={workspace_id}, err={e}"
                    )

            # 回退路径：通过 end_user_id 查关联配置
            if not end_user_id:
                logger.error("[FlushTask] workspace_id 和 end_user_id 均为空，无法加载配置")
                return None

            from app.services.memory_agent_service import get_end_user_connected_config

            with get_db_context() as db:
                connected_config = get_end_user_connected_config(end_user_id, db)

            config_id_raw = connected_config.get("memory_config_id")
            workspace_id_raw = connected_config.get("workspace_id")

            config_id = None
            if config_id_raw and config_id_raw != "None":
                try:
                    config_id = _uuid.UUID(str(config_id_raw))
                except (ValueError, AttributeError):
                    config_id = None

            fallback_workspace_id = None
            if workspace_id_raw and workspace_id_raw != "None":
                try:
                    fallback_workspace_id = _uuid.UUID(str(workspace_id_raw))
                except (ValueError, AttributeError):
                    fallback_workspace_id = None

            if config_id is None and fallback_workspace_id is None:
                logger.error(
                    f"[FlushTask] 无法解析 config_id 和 workspace_id: "
                    f"end_user_id={end_user_id}"
                )
                return None

            with get_db_context() as db:
                memory_config = MemoryConfigService(db).load_memory_config(
                    config_id=config_id,
                    workspace_id=fallback_workspace_id,
                    service_name="FlushTask",
                )

            return memory_config

        except Exception as e:
            logger.error(
                f"[FlushTask] 加载 memory_config 失败: "
                f"end_user_id={end_user_id}, workspace_id={workspace_id}, err={e}",
                exc_info=True,
            )
            return None

    # ──────────────────────────────────────────────
    # 辅助方法
    # ──────────────────────────────────────────────

