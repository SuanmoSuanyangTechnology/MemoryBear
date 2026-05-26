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
import uuid
from typing import List, Tuple

from sqlalchemy import select

from app.db import get_db_context
from app.models.app_model import App
from app.models.app_release_model import AppRelease
from app.models.conversation_model import Conversation
from app.models.memory_message_model import MemoryMessage

from app.core.memory.sliding_window.window_utils import (
    advance_write_cursor,
    message_to_dict,
)

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
        from app.core.memory.sliding_window.window_utils import unmark_conversation_pending

        logger.info(f"[FlushTask] 开始处理: conv={conversation_id}")

        try:
            await self._run_inner(conversation_id)
        finally:
            # 兜底清理 Redis Set：本次 flush 已尽力处理（无论成功/早退/异常）
            # 后续若有新消息写入，实时路径会重新 mark；若处理失败但还需处理，
            # ScanIdle 的 DB 回退查询（max_seq > write_cursor）仍能发现并派发。
            unmark_conversation_pending(conversation_id)

    async def _run_inner(self, conversation_id: str) -> None:
        """run() 的内部实现，提取出来便于 try/finally 统一收尾。"""
        # Step 1: 查询对话信息（end_user_id + workspace_id）
        conversation_info = self._get_conversation_info(conversation_id)
        if conversation_info is None:
            logger.error(f"[FlushTask] 对话不存在: conv={conversation_id}")
            return

        write_cursor, end_user_id, workspace_id = conversation_info

        # Step 1.5: 解析当前应用 release 中绑定的 memory_config_id
        # 路径: conversation.app_id → app.current_release_id → app_releases.config.memory.memory_config_id
        release_config_id = self._resolve_release_memory_config_id(conversation_id)
        if release_config_id is None:
            logger.warning(
                f"[FlushTask] 未能从应用发布配置解析到 memory_config_id，跳过 flush: "
                f"conv={conversation_id}, workspace={workspace_id}"
            )
            return

        # Step 1.6: 校验该 memory_config 仍然存在且处于活跃状态，避免下层 ERROR 日志噪音
        if not self._has_active_memory_config(release_config_id):
            logger.warning(
                f"[FlushTask] memory_config 不存在或未启用，跳过 flush: "
                f"conv={conversation_id}, config_id={release_config_id}"
            )
            return

        # Step 2: 通过 Layer 2 执行所有 pending user 消息
        # execute_pending_from_pool 内部处理：
        #   - should_memorize=FALSE → 推进 write_cursor
        #   - role=user + should_memorize=TRUE → 完整 WritePipeline.run_with_window()
        #   - role=assistant → 跳过（由下面的 Step 3 处理）
        from app.core.memory.sliding_window.window_utils import execute_pending_from_pool

        user_processed = await execute_pending_from_pool(
            conversation_id=conversation_id,
            end_user_id=end_user_id,
            config_id=str(release_config_id),
            workspace_id=workspace_id or "",
            enforce_window=False,  # 兜底路径，详见 execute_pending_from_pool docstring
        )
        logger.info(
            f"[FlushTask] Layer 2 完成 user 消息: conv={conversation_id}, processed={user_processed}"
        )

        # Step 3: 处理剩余的 assistant 消息（should_memorize=TRUE 的）
        # 重新查询 write_cursor（可能已被 execute_pending_from_pool 推进）
        write_cursor = self._get_write_cursor(conversation_id)
        if write_cursor is None:
            logger.info(f"[FlushTask] 无 write_cursor: conv={conversation_id}")
            return

        pending = self._get_pending_messages(conversation_id, write_cursor)
        assistant_messages = [
            m for m in pending
            if m.get("role") == "assistant" and m.get("should_memorize")
        ]

        if not assistant_messages:
            logger.info(f"[FlushTask] 处理完成: conv={conversation_id}")
            return

        memory_config = self._load_memory_config(config_id=release_config_id)
        if memory_config is None:
            logger.error(
                f"[FlushTask] 无法加载 memory_config 处理 assistant 消息: "
                f"conv={conversation_id}, config_id={release_config_id}"
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
            target_seq = message.get("message_seq")
            if target_seq is None:
                continue
            try:
                await pruning_pipeline.prune(
                    conversation_id=conversation_id,
                    message_seq=target_seq,
                    content=message.get("content") or "",
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
    # 内部方法：数据库查询（同步，使用 get_db_context）
    # ──────────────────────────────────────────────

    def _get_conversation_info(
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

    def _has_active_memory_config(self, config_id: uuid.UUID | None) -> bool:
        """检查指定 memory_config 是否存在。

        用于在执行 flush 前预校验，避免下层抛 ConfigurationError 产生 ERROR 日志噪音。
        校验自身出错时返回 True（不阻断主流程，让下层兜底处理）。

        说明：``MemoryConfig.state`` 表示"是否为 workspace 当前激活的默认配置"，
        而 release 中绑定的配置通常并非 workspace 默认（可能是用户自建配置），
        因此这里只检查存在性，不再过滤 ``state=True``。

        Args:
            config_id: release 中绑定的 memory_config_id（UUID）

        Returns:
            True 表示配置存在或校验过程异常；False 表示明确不存在应跳过。
        """
        if not config_id:
            return False

        try:
            from app.models.memory_config_model import MemoryConfig as MemoryConfigModel

            with get_db_context() as db:
                exists = db.execute(
                    select(MemoryConfigModel.config_id)
                    .where(MemoryConfigModel.config_id == config_id)
                    .limit(1)
                ).scalar_one_or_none()
                return exists is not None
        except Exception as e:
            logger.warning(
                f"[FlushTask] memory_config 预校验异常（继续执行）: "
                f"config_id={config_id}, err={e}",
                exc_info=True,
            )
            return True

    def _resolve_release_memory_config_id(
        self, conversation_id: str
    ) -> uuid.UUID | None:
        """从应用当前发布版本的 config 中解析 memory_config_id。

        查询链路：
            conversations.id → conversations.app_id
            → apps.current_release_id
            → app_releases.config["memory"]["memory_config_id"]

        通过 ``MemoryConfigService.extract_memory_config_id`` 解析 release 配置，
        从而兼容 agent / workflow 类型应用以及旧的 int 形态 config_id_old。

        Args:
            conversation_id: 对话 ID

        Returns:
            解析出的 memory_config_id（UUID）；未配置或解析失败时返回 None。
        """
        try:
            from app.services.memory_config_service import MemoryConfigService

            with get_db_context() as db:
                row = db.execute(
                    select(
                        App.id,
                        App.type,
                        App.current_release_id,
                        AppRelease.config,
                    )
                    .select_from(Conversation)
                    .join(App, App.id == Conversation.app_id)
                    .outerjoin(AppRelease, AppRelease.id == App.current_release_id)
                    .where(Conversation.id == conversation_id)
                ).one_or_none()

                if row is None:
                    logger.warning(
                        f"[FlushTask] 未找到对话对应的 app 或 release: conv={conversation_id}"
                    )
                    return None

                app_id, app_type, current_release_id, release_config = row

                if not current_release_id:
                    logger.warning(
                        f"[FlushTask] 应用尚未发布，无法解析 memory_config_id: "
                        f"conv={conversation_id}, app={app_id}"
                    )
                    return None

                if not isinstance(release_config, dict) or not release_config:
                    logger.warning(
                        f"[FlushTask] release.config 为空或格式异常: "
                        f"conv={conversation_id}, app={app_id}, "
                        f"release={current_release_id}"
                    )
                    return None

                config_id, is_legacy_int = MemoryConfigService(db).extract_memory_config_id(
                    app_type=str(app_type) if app_type else "",
                    config=release_config,
                )

                if config_id is None:
                    logger.warning(
                        f"[FlushTask] release.config 中未配置 memory_config_id: "
                        f"conv={conversation_id}, app={app_id}, "
                        f"release={current_release_id}, is_legacy_int={is_legacy_int}"
                    )
                    return None

                return config_id
        except Exception as e:
            logger.error(
                f"[FlushTask] 解析 release memory_config_id 异常: "
                f"conv={conversation_id}, err={e}",
                exc_info=True,
            )
            return None

    def _get_write_cursor(self, conversation_id: str) -> int | None:
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

    def _get_pending_messages(
        self, conversation_id: str, write_cursor: int
    ) -> List[dict]:
        """查询 memory_messages 表中 write_cursor 之后的所有未写入消息。

        查询 message_seq > write_cursor 的所有消息（包含 user 和 assistant），
        按 message_seq 升序排列。返回 dict 列表（在 with 块内转换，避免 session
        关闭后 ORM 对象 lazy-load 失败）。

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
                return [message_to_dict(m) for m in messages]
        except Exception as e:
            logger.error(
                f"[FlushTask] 查询待处理消息失败: "
                f"conv={conversation_id}, write_cursor={write_cursor}, err={e}",
                exc_info=True,
            )
            return []

    def _load_memory_config(self, config_id: uuid.UUID):
        """按指定 ``config_id`` 加载完整 ``MemoryConfig``。

        ``config_id`` 由 ``_resolve_release_memory_config_id`` 从应用 release
        中提取，绕过 workspace 默认配置。

        Args:
            config_id: 要加载的记忆配置 UUID

        Returns:
            MemoryConfig 对象；加载失败时返回 None。
        """
        try:
            from app.services.memory_config_service import MemoryConfigService

            with get_db_context() as db:
                return MemoryConfigService(db).load_memory_config(
                    config_id=config_id,
                    workspace_id=None,
                    service_name="FlushTask",
                )
        except Exception as e:
            logger.error(
                f"[FlushTask] 加载 memory_config 失败: config_id={config_id}, err={e}",
                exc_info=True,
            )
            return None

    # ──────────────────────────────────────────────
    # 辅助方法
    # ──────────────────────────────────────────────

