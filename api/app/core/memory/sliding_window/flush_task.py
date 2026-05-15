"""
FlushTask — 兜底写入任务

职责：
- 调用 Layer 2 (execute_pending_from_pool) 处理 user 消息
- should_memorize=TRUE 的 assistant 消息单独调用 PruningPipeline.prune()
- 某条消息处理异常时：记录日志，跳过该条，继续处理后续消息，不回滚已写入消息

Requirements: 4.3, 4.4, 4.5
"""

from __future__ import annotations

import logging
from typing import List, Optional

from sqlalchemy import select

from app.db import get_db_context
from app.models.conversation_model import Conversation
from app.models.memory_message_model import MemoryMessage

from app.core.memory.sliding_window.window_utils import advance_write_cursor

logger = logging.getLogger(__name__)


class FlushTask:
    """兜底写入任务。

    委托 Layer 2 (execute_pending_from_pool) 处理 user 消息，
    自行处理 assistant 消息（PruningPipeline）。
    由 Celery Beat 定时扫描触发。
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

        # Step 1: 查询对话信息（end_user_id + workspace_id）
        conversation_info = await self._get_conversation_info(conversation_id)
        if conversation_info is None:
            logger.error(f"[FlushTask] 对话不存在: conv={conversation_id}")
            return

        write_cursor, end_user_id, workspace_id = conversation_info

        # Step 2: 通过 Layer 2 执行所有 pending user 消息
        # execute_pending_from_pool 内部处理：
        #   - should_memorize=FALSE → 推进 write_cursor
        #   - role=user + should_memorize=TRUE → 完整 WritePipeline.run_with_window()
        #   - role=assistant → 跳过（由下面的 Step 3 处理）
        from app.core.memory.sliding_window.window_utils import execute_pending_from_pool

        user_processed = await execute_pending_from_pool(
            conversation_id=conversation_id,
            end_user_id=end_user_id,
            workspace_id=workspace_id,
        )
        logger.info(
            f"[FlushTask] Layer 2 完成 user 消息: conv={conversation_id}, processed={user_processed}"
        )

        # Step 3: 处理剩余的 assistant 消息（should_memorize=TRUE 的）
        # 重新查询 write_cursor（可能已被 execute_pending_from_pool 推进）
        write_cursor = await self._get_write_cursor(conversation_id)
        if write_cursor is None:
            logger.info(f"[FlushTask] 无 write_cursor: conv={conversation_id}")
            return

        pending = await self._get_pending_messages(conversation_id, write_cursor)
        assistant_messages = [
            m for m in pending
            if m.role == "assistant" and m.should_memorize
        ]

        if not assistant_messages:
            logger.info(f"[FlushTask] 处理完成: conv={conversation_id}")
            return

        memory_config = await self._load_memory_config(
            end_user_id=end_user_id,
            workspace_id=workspace_id,
        )
        if memory_config is None:
            logger.error(
                f"[FlushTask] 无法加载 memory_config 处理 assistant 消息: conv={conversation_id}"
            )
            return

        language = str(getattr(memory_config, "language", "zh") or "zh")

        from app.core.memory.pipelines.pruning_pipeline import PruningPipeline
        pruning_pipeline = PruningPipeline(
            memory_config=memory_config,
            end_user_id=end_user_id,
            language=language,
        )

        for message in assistant_messages:
            target_seq = message.message_seq
            if target_seq is None:
                continue
            try:
                await pruning_pipeline.prune(
                    conversation_id=conversation_id,
                    message_seq=target_seq,
                    content=message.content or "",
                )
                await advance_write_cursor(conversation_id, target_seq)
                logger.info(
                    f"[FlushTask] assistant 消息剪枝完成: "
                    f"conv={conversation_id}, seq={target_seq}"
                )
            except Exception as e:
                logger.error(
                    f"[FlushTask] assistant 消息处理异常，跳过: "
                    f"conv={conversation_id}, seq={target_seq}, err={e}",
                    exc_info=True,
                )
                continue

        logger.info(f"[FlushTask] 处理完成: conv={conversation_id}")

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

    async def _get_write_cursor(self, conversation_id: str) -> int | None:
        """查询 write_cursor。"""
        try:
            with get_db_context() as db:
                return db.execute(
                    select(Conversation.write_cursor).where(
                        Conversation.id == conversation_id
                    )
                ).scalar_one_or_none()
        except Exception as e:
            logger.error(
                f"[FlushTask] 查询 write_cursor 失败: conv={conversation_id}, err={e}",
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

