"""
MemoryService — 记忆模块统一入口（Facade）

所有外部调用方（controllers、Celery tasks、API service、Agent 对话、Workflow MemoryWriteNode）
只依赖此类。

职责：
- 接收已加载的 MemoryConfig，选择并调用对应的 Pipeline
- 检查应用级记忆门禁（memory.enabled）
- 将消息写入 memory_messages 表
- 分派给 SlidingWindowScheduler
- 不包含任何业务逻辑实现
- 不直接操作数据库或 LLM（除 memory_messages 写入外）

依赖方向：外部调用方 → MemoryService → Pipeline → Engine → Repository
"""

import logging
import uuid
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, List, Optional

from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from app.models.conversation_model import Message

# Runtime import — used in sync_agent_message and write_workflow_messages
from app.models.memory_message_model import MemoryMessage

from app.core.memory.enums import SearchStrategy, StorageType
from app.core.memory.models.message_models import DialogData
from app.core.memory.models.service_models import MemoryContext, MemorySearchResult
from app.core.memory.pipelines.memory_read import ReadPipeLine
from app.core.memory.pipelines.pilot_write_pipeline import PilotWriteResult
from app.core.memory.pipelines.write_pipeline import WriteResult
from app.db import get_db_context
from app.services.memory_config_service import MemoryConfigService

logger = logging.getLogger(__name__)


class MemoryService:
    """记忆模块统一入口

    所有外部调用方（controllers、Celery tasks、API service）只依赖此类。

    设计决策：
    - __init__ 接收已加载的 MemoryConfig（而非 config_id），
      配置加载的职责留在调用方（MemoryAgentService），
      因为调用方需要 config 做其他事情（如感知记忆处理）。
    - 未实现的方法抛出 NotImplementedError，明确标记待实现状态。
    """

    def __init__(
            self,
            db: Session,
            config_id: str | None,
            end_user_id: str,
            workspace_id: str | None = None,
            storage_type: str = "neo4j",
            user_rag_memory_id: str | None = None,
            language: str = "zh",
    ):
        config_service = MemoryConfigService(db)
        memory_config = None
        if config_id is not None and config_id != "":
            memory_config = config_service.load_memory_config(
                config_id=config_id,
                workspace_id=workspace_id,
                service_name="MemoryService",
            )
        if memory_config is None and storage_type.lower() == "neo4j":
            logger.warning(
                "MemoryService 初始化时未提供 memory config（config_id=None），"
                "仅 sync_agent_message / write_workflow_messages 可用，"
                "write/read/pilot_write 方法将不可用"
            )
        self.ctx = MemoryContext(
            end_user_id=end_user_id,
            memory_config=memory_config,
            storage_type=StorageType(storage_type),
            user_rag_memory_id=user_rag_memory_id,
            language=language,
        )

    async def write(
            self,
            messages: List[dict],
            language: str = "zh",
            ref_id: str = "",
            is_pilot_run: bool = False,
            progress_callback: Optional[
                Callable[[str, str, Optional[Dict[str, Any]]], Awaitable[None]]
            ] = None,
    ) -> "WriteResult":
        """写入记忆：对话 → 萃取 → 存储 → 聚类 → 摘要

        Args:
            messages: 结构化消息 [{"role": "user"/"assistant", "content": "...", "dialog_at": "..."}]
            language: 语言 ("zh" | "en")
            ref_id: 引用 ID，为空则自动生成
            is_pilot_run: 试运行模式（只萃取不写入）
            progress_callback: 可选的进度回调

        Returns:
            WriteResult 包含状态和统计信息
        """
        if self.ctx.memory_config is None:
            raise RuntimeError("MemoryService.write() 需要 memory_config，但当前实例未加载配置")
        from app.core.memory.pipelines.write_pipeline import WritePipeline

        pipeline = WritePipeline(
            memory_config=self.ctx.memory_config,
            end_user_id=self.ctx.end_user_id,
            language=language,
            progress_callback=progress_callback,
        )
        return await pipeline.run(
            messages=messages,
            ref_id=ref_id,
            is_pilot_run=is_pilot_run,
        )

    async def pilot_write(
            self,
            chunked_dialogs: List["DialogData"],
            language: str = "zh",
            progress_callback: Optional[
                Callable[[str, str, Optional[Dict[str, Any]]], Awaitable[None]]
            ] = None,
    ) -> "PilotWriteResult":
        """试运行写入：只执行萃取链路，不写入 Neo4j

        Args:
            chunked_dialogs: 预处理 + 分块后的 DialogData 列表
            language: 语言 ("zh" | "en")
            progress_callback: 可选的进度回调

        Returns:
            PilotWriteResult 包含萃取结果、图构建结果和去重结果
        """
        from app.core.memory.pipelines.pilot_write_pipeline import PilotWritePipeline

        if self.ctx.memory_config is None:
            raise RuntimeError("MemoryService.pilot_write() 需要 memory_config，但当前实例未加载配置")
        pipeline = PilotWritePipeline(
            memory_config=self.ctx.memory_config,
            end_user_id=self.ctx.end_user_id,
            language=language,
            progress_callback=progress_callback,
        )
        return await pipeline.run(chunked_dialogs)

    async def read(
            self,
            query: str,
            search_switch: SearchStrategy,
            history: list | None = None,
            limit: int = 10,
    ) -> MemorySearchResult:
        if history is None:
            history = []
        if self.ctx.memory_config is None:
            raise RuntimeError("MemoryService.read() 需要 memory_config，但当前实例未加载配置")
        with get_db_context() as db:
            return await ReadPipeLine(self.ctx, db).run(query, search_switch, history, limit)

    async def forget(
            self, max_batch: int = 100, min_days: int = 30
    ) -> dict:
        """遗忘：识别低激活节点并融合"""
        raise NotImplementedError("ForgettingPipeline 尚未实现")

    async def run_reflection_layer2(self, baseline: str = "HYBRID", language: str = "zh") -> dict:
        """反思引擎 Layer 2 离线巡检

        由 Celery 定时任务调用（每 10 分钟），执行描述合并等子问题。
        """
        from app.core.memory.pipelines.reflection_pipeline import ReflectionPipeline

        pipeline = ReflectionPipeline(
            memory_config=self.ctx.memory_config,
            end_user_id=self.ctx.end_user_id,
            language=language,
        )
        return await pipeline.run_layer2(baseline=baseline)

    async def run_reflection_layer3(self) -> dict:
        """反思引擎 Layer 3 知识综合

        由 Celery 定时任务调用（每天一次）。
        TODO: Observation 合成、Opinion 演化、模式反馈
        """
        raise NotImplementedError("Layer 3 尚未实现")

    # async def cluster(self, new_entity_ids: list[str] = None) -> None:
    #     """聚类：全量初始化或增量更新社区"""
    #     raise NotImplementedError("ClusteringPipeline 尚未实现")

    # ──────────────────────────────────────────────
    # 统一门户：Agent 对话消息同步
    # ──────────────────────────────────────────────

    @classmethod
    async def sync_message(
        cls,
        conversation_id: str,
        message: "Message",
        app_id: str,
        is_draft: bool = False,
        config_id: str = "",
        workspace_id: str = "",
    ) -> Optional["MemoryMessage"]:
        """Agent 对话消息同步到 memory_messages 表（类方法，无需实例化）。

        不依赖 memory_config，专供 conversation_service.py 调用。
        内部直接操作 memory_messages 表并分派 SlidingWindowScheduler。

        1. 检查 app_releases.config.memory.enabled（草稿会话使用草稿配置）
        2. 若 false → 返回 None，消息不进入 memory_messages
        3. 若 true → 写入 memory_messages（should_memorize=TRUE）
        4. 刷新 Redis 活跃 key
        5. 分派给 SlidingWindowScheduler

        Args:
            conversation_id: 会话 ID
            message: Message ORM 对象（已持久化到 messages 表）
            app_id: 应用 ID，用于检查 memory.enabled
            is_draft: 是否为草稿会话
            config_id: 记忆配置 ID（传给 Scheduler，可为空）
            workspace_id: 工作空间 ID

        Returns:
            MemoryMessage 实例若成功写入，否则 None
        """
        # Step 0: 检查应用级记忆门禁
        if not await cls._check_memory_enabled(app_id, is_draft):
            logger.debug(
                f"[MemoryService] memory.enabled=false，跳过: "
                f"conv={conversation_id}, app={app_id}, is_draft={is_draft}"
            )
            return None

        # Step 1: 写入 memory_messages 表
        try:
            with get_db_context() as db:
                memory_msg = MemoryMessage(
                    id=uuid.uuid4(),
                    conversation_id=uuid.UUID(str(conversation_id)),
                    original_message_id=message.id,
                    role=message.role,
                    content=message.content,
                    message_seq=message.message_seq,
                    should_memorize=True,
                    created_at=message.created_at,
                )
                db.add(memory_msg)
                db.commit()
                logger.debug(
                    f"[MemoryService] MemoryMessage 已写入: "
                    f"conv={conversation_id}, seq={message.message_seq}, role={message.role}"
                )
        except Exception as e:
            logger.error(
                f"[MemoryService] 写入 memory_messages 失败: "
                f"conv={conversation_id}, seq={message.message_seq}, err={e}",
                exc_info=True,
            )
            return None

        # Step 2: 刷新 Redis 活跃 key
        await cls._refresh_active_key(conversation_id)

        # Step 3: 分派给 SlidingWindowScheduler
        from app.core.memory.sliding_window.window_utils import dispatch_to_scheduler

        await dispatch_to_scheduler(
            conversation_id=str(conversation_id),
            config_id=config_id,
            workspace_id=workspace_id,
        )

        return memory_msg

    async def sync_agent_message(
        self,
        conversation_id: str,
        message: "Message",
        app_id: str,
        is_draft: bool = False,
        config_id: str = "",
        workspace_id: str = "",
    ) -> Optional["MemoryMessage"]:
        """Agent 对话消息同步到 memory_messages 表。

        1. 检查 app_releases.config.memory.enabled（草稿会话使用草稿配置）
        2. 若 false → 返回 None，消息不进入 memory_messages
        3. 若 true → 写入 memory_messages（should_memorize=TRUE）
        4. 刷新 Redis 活跃 key（conv_active:{conversation_id}）
        5. 分派给 SlidingWindowScheduler.check_and_dispatch()

        Args:
            conversation_id: 会话 ID
            message: Message ORM 对象（已持久化到 messages 表）
            app_id: 应用 ID，用于检查 memory.enabled
            is_draft: 是否为草稿会话
            config_id: 记忆配置 ID
            workspace_id: 工作空间 ID

        Returns:
            MemoryMessage 实例若成功写入，否则 None
        """
        # Step 0: 检查应用级记忆门禁
        if not await self._check_memory_enabled(app_id, is_draft):
            logger.debug(
                f"[MemoryService] memory.enabled=false，跳过: "
                f"conv={conversation_id}, app={app_id}, is_draft={is_draft}"
            )
            return None

        # Step 1: 写入 memory_messages 表
        try:
            with get_db_context() as db:
                memory_msg = MemoryMessage(
                    id=uuid.uuid4(),
                    conversation_id=uuid.UUID(str(conversation_id)),
                    original_message_id=message.id,
                    role=message.role,
                    content=message.content,
                    message_seq=message.message_seq,
                    should_memorize=True,
                    created_at=message.created_at,
                )
                db.add(memory_msg)
                db.commit()
                logger.debug(
                    f"[MemoryService] MemoryMessage 已写入: "
                    f"conv={conversation_id}, seq={message.message_seq}, role={message.role}"
                )
        except Exception as e:
            logger.error(
                f"[MemoryService] 写入 memory_messages 失败: "
                f"conv={conversation_id}, seq={message.message_seq}, err={e}",
                exc_info=True,
            )
            return None

        # Step 2: 刷新 Redis 活跃 key
        await self._refresh_active_key(conversation_id)

        # Step 3: 分派给 SlidingWindowScheduler
        from app.core.memory.sliding_window.window_utils import dispatch_to_scheduler

        await dispatch_to_scheduler(
            conversation_id=str(conversation_id),
            config_id=config_id,
            end_user_id=self.ctx.end_user_id,
            workspace_id=workspace_id,
            language=self.ctx.memory_config.language if self.ctx.memory_config else "zh",
        )

        return memory_msg

    async def sync_agent_messages_batch(
        self,
        conversation_id: str,
        messages: List["Message"],
        app_id: str,
        is_draft: bool = False,
        config_id: str = "",
        workspace_id: str = "",
    ) -> List["MemoryMessage"]:
        """批量同步 Agent 对话的消息到 memory_messages 表。

        内部逐条调用 sync_agent_message。

        Args:
            conversation_id: 会话 ID
            messages: Message ORM 对象列表（已持久化到 messages 表）
            app_id: 应用 ID
            is_draft: 是否为草稿会话
            config_id: 记忆配置 ID
            workspace_id: 工作空间 ID

        Returns:
            成功写入的 MemoryMessage 实例列表
        """
        results = []
        for message in messages:
            mm = await self.sync_agent_message(
                conversation_id=conversation_id,
                message=message,
                app_id=app_id,
                is_draft=is_draft,
                config_id=config_id,
                workspace_id=workspace_id,
            )
            if mm is not None:
                results.append(mm)
        return results

    # ──────────────────────────────────────────────
    # 统一门户：工作流 MemoryWriteNode 消息写入
    # ──────────────────────────────────────────────

    async def write_workflow_messages(
        self,
        conversation_id: str,
        messages: List[dict],
        config_id: str = "",
        end_user_id: str = "",
        workspace_id: str = "",
        language: str = "zh",
    ) -> List["MemoryMessage"]:
        """工作流 MemoryWriteNode 消息写入 memory_messages 表。

        1. 将消息写入 memory_messages 表（should_memorize 强制 TRUE）
        2. 分派给 SlidingWindowScheduler

        Args:
            conversation_id: 会话 ID
            messages: 消息列表 [{"role": "user"|"assistant", "content": "...", "files": [...]}]
            config_id: 记忆配置 ID
            end_user_id: 终端用户 ID
            workspace_id: 工作空间 ID
            language: 语言

        Returns:
            成功写入的 MemoryMessage 实例列表
        """
        from app.core.memory.sliding_window.window_utils import (
            write_batch_to_memory_messages,
            dispatch_to_scheduler,
        )

        written = await write_batch_to_memory_messages(
            conversation_id=conversation_id,
            messages=messages,
        )

        if written:
            await dispatch_to_scheduler(
                conversation_id=conversation_id,
                config_id=config_id,
                end_user_id=end_user_id,
                workspace_id=workspace_id,
                language=language,
            )

        return written

    # ──────────────────────────────────────────────
    # 内部辅助方法
    # ──────────────────────────────────────────────

    @staticmethod
    async def _check_memory_enabled(app_id: str, is_draft: bool) -> bool:
        """查询 app_releases.config -> 'memory' ->> 'enabled'。

        草稿会话使用草稿配置，发布会话使用发布版本配置。
        返回 False 若 memory 键不存在或 enabled 为 false。

        Args:
            app_id: 应用 ID
            is_draft: 是否为草稿会话

        Returns:
            True 表示该应用启用了记忆功能
        """
        try:
            from sqlalchemy import select as sa_select
            from app.models.app_release_model import AppRelease

            with get_db_context() as db:
                if is_draft:
                    # 草稿会话：查询草稿版本（is_active=False 且最新）
                    result = db.execute(
                        sa_select(AppRelease.config)
                        .where(
                            AppRelease.app_id == uuid.UUID(str(app_id)),
                            AppRelease.is_active.is_(False),
                        )
                        .order_by(AppRelease.created_at.desc())
                        .limit(1)
                    ).scalar_one_or_none()
                else:
                    # 发布会话：查询当前活跃版本（取最新版本，避免多个 is_active=True 记录导致 MultipleResultsFound）
                    result = db.execute(
                        sa_select(AppRelease.config)
                        .where(
                            AppRelease.app_id == uuid.UUID(str(app_id)),
                            AppRelease.is_active.is_(True),
                        )
                        .order_by(AppRelease.version.desc())
                        .limit(1)
                    ).scalar_one_or_none()

                config = result or {}
                memory_config = config.get("memory", {}) if isinstance(config, dict) else {}
                return bool(memory_config.get("enabled", False))
        except Exception as e:
            logger.warning(
                f"[MemoryService] 检查 memory.enabled 失败，默认返回 False: "
                f"app={app_id}, is_draft={is_draft}, err={e}",
                exc_info=True,
            )
            return False

    @staticmethod
    async def _refresh_active_key(conversation_id: str) -> None:
        """刷新对话活跃 key 的 TTL。

        执行 SETEX conv_active:{conversation_id} 300 1，表示对话仍在活跃状态。
        key 过期（300 秒内无新消息）即代表对话空闲，触发 Flush_Task。

        Args:
            conversation_id: 对话 ID
        """
        try:
            from app.aioRedis import get_thread_safe_redis

            redis_client = get_thread_safe_redis()
            key = f"conv_active:{conversation_id}"
            await redis_client.set(key, "1", ex=300)
            logger.debug(
                f"[MemoryService] 活跃 key 已刷新: key={key}, ttl=300s"
            )
        except Exception as e:
            logger.warning(
                f"[MemoryService] 刷新活跃 key 失败（不影响主流程）: "
                f"conv={conversation_id}, err={e}",
                exc_info=True,
            )
