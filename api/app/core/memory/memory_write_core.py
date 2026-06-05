"""MemoryWriteCore — 三种触发器共享的写入核心。

三个 Celery 任务（memory_write.api / .window / .flush）作为薄壳调用此模块，
所有锁获取、配置解析、ContextVar 重入、循环清理逻辑在这里统一封装。

设计参考：docs/20260604_lock-flush_conversation/celery_refactor_implementation_plan.md §4.1

职责：
  1. 解析 end_user_id（缺失时从 conversations.user_id 反查）
  2. 解析 config_id（conversations.memory_config_id → release 兜底）
  3. 抢 RedisFairLock(memory_write:{end_user_id}) + ContextVar 重入标记
  4. 加载 memory_config
  5. 调用 execute_pending_from_pool(enforce_window)
  6. include_assistant_prune=True 时调用 PruningPipeline 处理 assistant
  7. unmark_conversation_pending（如已清空）
  8. finally：释放锁 + 重置 ContextVar + asyncio 清理

不包含：
  - Celery 任务注册（在 tasks.py 中）
  - 任务参数解析（由任务薄壳负责）
  - Neo4j 内部事务重试（在 WritePipeline._store 中）
"""

from __future__ import annotations

import logging
import time
import uuid as _uuid
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import select

from app.core.logging_config import get_logger
from app.db import get_db_context
from app.models.conversation_model import Conversation

logger = get_logger(__name__)


@dataclass
class WriteResult:
    """MemoryWriteCore.write_pending 的统一返回值。"""

    status: str = "SUCCESS"         # "SUCCESS" | "SKIPPED" | "FAILURE"
    trigger: str = "unknown"        # "api" | "window" | "flush"
    conversation_id: str = ""
    end_user_id: Optional[str] = None
    config_id: Optional[str] = None
    processed: int = 0
    elapsed_time: float = 0.0
    error: Optional[str] = None


class MemoryWriteCore:
    """三种触发器共享的写入核心。无状态，所有上下文通过参数传入。"""

    # ──────────────────────────────────────────────
    # 公开接口
    # ──────────────────────────────────────────────

    @staticmethod
    async def write_pending(
        *,
        trigger: str,
        conversation_id: str,
        end_user_id: Optional[str] = None,
        config_id: Optional[str] = None,
        enforce_window: bool = True,
        include_assistant_prune: bool = False,
    ) -> WriteResult:
        """统一写入入口。

        Args:
            trigger: "api" / "window" / "flush"（仅用于日志和返回值）
            conversation_id: 必填
            end_user_id: 可选；缺失时从 conversations.user_id 反查
            config_id: 可选；缺失时从 conversations.memory_config_id 读取，
                       再缺失走 release 回退
            enforce_window: 是否要求下文 ≥ 3 条 user Q 才处理
            include_assistant_prune: 是否在写完 user 消息后跑 PruningPipeline 处理 assistant

        Returns:
            WriteResult dataclass
        """
        start_time = time.time()
        result = WriteResult(
            trigger=trigger,
            conversation_id=conversation_id,
        )

        # 1. 解析 end_user_id
        if not end_user_id:
            end_user_id = MemoryWriteCore.resolve_end_user_id(conversation_id)
        if not end_user_id:
            result.status = "FAILURE"
            result.error = "无法解析 end_user_id"
            result.elapsed_time = time.time() - start_time
            logger.error(
                f"[MemoryWrite][{trigger}] 无法解析 end_user_id: conv={conversation_id}"
            )
            return result
        result.end_user_id = end_user_id

        # 2. 解析 config_id
        if not config_id:
            config_id = MemoryWriteCore.resolve_config_id(conversation_id)
        result.config_id = config_id

        # 3. 抢锁 + ContextVar
        from app.tasks import get_sync_redis_client
        from app.utils.redis_lock import RedisFairLock
        from app.services.memory_agent_service import (
            _set_write_lock_holder,
            _reset_write_lock_holder,
        )

        redis_client = get_sync_redis_client()
        lock = None
        lock_token = None

        if redis_client is not None:
            lock = RedisFairLock(
                key=f"memory_write:{end_user_id}",
                redis_client=redis_client,
                expire=600,
                timeout=3600,
                auto_renewal=True,
            )
            if not lock.acquire():
                logger.warning(
                    f"[MemoryWrite][{trigger}] 获取锁超时: "
                    f"conv={conversation_id}, end_user_id={end_user_id}"
                )
                result.status = "SKIPPED"
                result.error = "acquire lock timeout"
                result.elapsed_time = time.time() - start_time
                return result

            lock_token = _set_write_lock_holder(end_user_id)

        try:
            # 4. 加载 memory_config + 执行写入
            processed = await MemoryWriteCore._execute(
                conversation_id=conversation_id,
                end_user_id=end_user_id,
                config_id=config_id,
                enforce_window=enforce_window,
                include_assistant_prune=include_assistant_prune,
                trigger=trigger,
            )
            result.processed = processed
            result.status = "SUCCESS"

        except Exception as e:
            result.status = "FAILURE"
            result.error = str(e)
            logger.error(
                f"[MemoryWrite][{trigger}] 执行失败: "
                f"conv={conversation_id}, end_user_id={end_user_id}, err={e}",
                exc_info=True,
            )

        finally:
            if lock_token is not None:
                try:
                    _reset_write_lock_holder(lock_token)
                except Exception as e:
                    logger.warning(f"[MemoryWrite][{trigger}] 重置锁标记失败: {e}")
            if lock is not None:
                try:
                    lock.release()
                except Exception as e:
                    logger.warning(f"[MemoryWrite][{trigger}] 释放锁失败: {e}")

        result.elapsed_time = time.time() - start_time
        return result


    # ──────────────────────────────────────────────
    # 内部执行逻辑
    # ──────────────────────────────────────────────

    @staticmethod
    async def _execute(
        *,
        conversation_id: str,
        end_user_id: str,
        config_id: Optional[str],
        enforce_window: bool,
        include_assistant_prune: bool,
        trigger: str,
    ) -> int:
        """持锁后的核心执行逻辑。

        1. 加载 memory_config
        2. execute_pending_from_pool（处理 user 消息）
        3. 如果 include_assistant_prune=True，处理 assistant 消息剪枝

        Returns:
            处理的消息数
        """
        from app.services.memory_config_service import MemoryConfigService
        from app.core.memory.sliding_window.window_utils import (
            execute_pending_from_pool,
        )

        # 加载 memory_config
        _config_id = _uuid.UUID(config_id) if config_id else None
        workspace_id = MemoryWriteCore._get_workspace_id(conversation_id)
        _workspace_id = _uuid.UUID(workspace_id) if workspace_id else None

        with get_db_context() as db:
            memory_config = MemoryConfigService(db).load_memory_config(
                config_id=_config_id,
                workspace_id=_workspace_id,
                service_name=f"MemoryWriteCore.{trigger}",
            )

        # 执行 user 消息写入
        processed = await execute_pending_from_pool(
            conversation_id=conversation_id,
            end_user_id=end_user_id,
            config_id=config_id or "",
            workspace_id=workspace_id or "",
            enforce_window=enforce_window,
        )

        # 如果需要处理 assistant 消息剪枝（flush 模式）
        if include_assistant_prune:
            await MemoryWriteCore._prune_assistants(
                conversation_id=conversation_id,
                end_user_id=end_user_id,
                memory_config=memory_config,
            )

        return processed

    @staticmethod
    async def _prune_assistants(
        *,
        conversation_id: str,
        end_user_id: str,
        memory_config,
    ) -> None:
        """处理剩余的 should_memorize=TRUE 的 assistant 消息。

        从 FlushTask 中提取的逻辑：查询 write_cursor 后的 assistant 消息，
        逐条调用 PruningPipeline.prune()。
        """
        from app.core.memory.sliding_window.window_utils import (
            advance_write_cursor,
            message_to_dict,
        )
        from app.core.memory.pipelines.pruning_pipeline import PruningPipeline
        from app.models.memory_message_model import MemoryMessage

        # 查询当前 write_cursor 后的 assistant 消息
        with get_db_context() as db:
            write_cursor = db.execute(
                select(Conversation.write_cursor).where(
                    Conversation.id == conversation_id
                )
            ).scalar_one_or_none()

            if write_cursor is None:
                return

            messages = (
                db.execute(
                    select(MemoryMessage)
                    .where(
                        MemoryMessage.conversation_id == conversation_id,
                        MemoryMessage.message_seq > write_cursor,
                        MemoryMessage.role == "assistant",
                    )
                    .order_by(MemoryMessage.message_seq.asc())
                )
                .scalars()
                .all()
            )
            assistant_messages = [
                message_to_dict(m) for m in messages
                if m.should_memorize
            ]

        if not assistant_messages:
            return

        language = str(getattr(memory_config, "language", "zh") or "zh")
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
                    f"[MemoryWrite][flush] assistant 剪枝完成: "
                    f"conv={conversation_id}, seq={target_seq}"
                )
            except Exception as e:
                logger.error(
                    f"[MemoryWrite][flush] assistant 剪枝异常，跳过: "
                    f"conv={conversation_id}, seq={target_seq}, err={e}",
                    exc_info=True,
                )
                continue


    # ──────────────────────────────────────────────
    # 辅助方法：配置解析
    # ──────────────────────────────────────────────

    @staticmethod
    def resolve_end_user_id(conversation_id: str) -> Optional[str]:
        """从 conversations.user_id 反查 end_user_id。"""
        try:
            with get_db_context() as db:
                result = db.execute(
                    select(Conversation.user_id).where(
                        Conversation.id == conversation_id
                    )
                ).scalar_one_or_none()
                return str(result) if result else None
        except Exception as e:
            logger.error(
                f"[MemoryWriteCore] 反查 end_user_id 失败: "
                f"conv={conversation_id}, err={e}",
                exc_info=True,
            )
            return None

    @staticmethod
    def resolve_config_id(conversation_id: str) -> Optional[str]:
        """配置解析三层兜底：

        1. conversations.memory_config_id（per-conversation 锁定）
        2. release.config["memory"]["memory_config_id"]（兼容旧数据）
        3. 返回 None（调用方决定是否报错）
        """
        try:
            with get_db_context() as db:
                # 第一层：从 conversation 上直接读
                conv = db.execute(
                    select(
                        Conversation.memory_config_id,
                        Conversation.app_id,
                    ).where(Conversation.id == conversation_id)
                ).one_or_none()

                if conv is None:
                    return None

                if conv.memory_config_id is not None:
                    return str(conv.memory_config_id)

                # 第二层：从 release 解析
                from app.models.app_model import App
                from app.models.app_release_model import AppRelease
                from app.services.memory_config_service import MemoryConfigService

                row = db.execute(
                    select(App.type, App.current_release_id, AppRelease.config)
                    .outerjoin(AppRelease, AppRelease.id == App.current_release_id)
                    .where(App.id == conv.app_id)
                ).one_or_none()

                if row is None or not row.current_release_id:
                    return None

                release_config = row.config
                if not isinstance(release_config, dict):
                    return None

                config_id, _ = MemoryConfigService(db).extract_memory_config_id(
                    app_type=str(row.type) if row.type else "",
                    config=release_config,
                )
                return str(config_id) if config_id else None

        except Exception as e:
            logger.error(
                f"[MemoryWriteCore] 解析 config_id 失败: "
                f"conv={conversation_id}, err={e}",
                exc_info=True,
            )
            return None

    @staticmethod
    def _get_workspace_id(conversation_id: str) -> Optional[str]:
        """从 conversations.workspace_id 反查。"""
        try:
            with get_db_context() as db:
                result = db.execute(
                    select(Conversation.workspace_id).where(
                        Conversation.id == conversation_id
                    )
                ).scalar_one_or_none()
                return str(result) if result else None
        except Exception as e:
            logger.warning(
                f"[MemoryWriteCore] 反查 workspace_id 失败: "
                f"conv={conversation_id}, err={e}"
            )
            return None

    @staticmethod
    async def lock_config_for_conversation(
        conversation_id: str,
        config_id: str,
    ) -> None:
        """首次写入时把 config_id 锁定到 conversations 表。

        仅当 conversations.memory_config_id 为 NULL 时才 UPDATE，
        已锁定则跳过（幂等）。

        调用时机：
        - Agent / Workflow 对话：首次 MemoryService.sync_message 时调用
        - API service：create_new_api_conversation 时直接写入，不需要调这个
        """
        try:
            from sqlalchemy import update

            with get_db_context() as db:
                db.execute(
                    update(Conversation)
                    .where(
                        Conversation.id == conversation_id,
                        Conversation.memory_config_id.is_(None),
                    )
                    .values(memory_config_id=_uuid.UUID(config_id))
                )
                db.commit()
        except Exception as e:
            logger.warning(
                f"[MemoryWriteCore] 锁定 config_id 失败（不影响主流程）: "
                f"conv={conversation_id}, config_id={config_id}, err={e}"
            )
