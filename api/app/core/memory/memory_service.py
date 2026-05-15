"""
MemoryService — 记忆模块统一入口（Facade）

所有外部调用方（controllers、Celery tasks、API service）只依赖此类。

职责：
- 接收已加载的 MemoryConfig，选择并调用对应的 Pipeline
- 不包含任何业务逻辑实现
- 不直接操作数据库或 LLM

依赖方向：外部调用方 → MemoryService → Pipeline → Engine → Repository
"""

import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

from sqlalchemy.orm import Session

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
        if config_id is not None:
            memory_config = config_service.load_memory_config(
                config_id=config_id,
                workspace_id=workspace_id,
                service_name="MemoryService",
            )
        if memory_config is None and storage_type.lower() == "neo4j":
            raise RuntimeError("Memory configuration for unspecified users")
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
