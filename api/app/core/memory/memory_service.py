"""
MemoryService — 记忆模块统一入口（Facade）

所有外部调用方（controllers、Celery tasks、API service）只依赖此类。

职责：
- 接收已加载的 MemoryConfig，选择并调用对应的 Pipeline
- 不包含任何业务逻辑实现
- 不直接操作数据库或 LLM

依赖方向：外部调用方 → MemoryService → Pipeline → Engine → Repository
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, List, Optional

if TYPE_CHECKING:
    from app.core.memory.pipelines.pilot_write_pipeline import PilotWriteResult
    from app.core.memory.pipelines.write_pipeline import WriteResult
    from app.core.memory.models.message_models import DialogData
    from app.schemas.memory_config_schema import MemoryConfig

from app.core.memory.enums import SearchStrategy
from app.core.memory.models.service_models import MemorySearchResult
from app.core.memory.pipelines.memory_read import ReadPipeLine
from app.db import get_db_context

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
        memory_config: MemoryConfig,
        end_user_id: str,
    ):
        """
        Args:
            memory_config: 已加载的不可变配置对象
            end_user_id: 终端用户 ID
        """
        self.memory_config = memory_config
        self.end_user_id = end_user_id

    async def write(
        self,
        messages: List[dict],
        language: str = "zh",
        ref_id: str = "",
        is_pilot_run: bool = False,
        progress_callback: Optional[
            Callable[[str, str, Optional[Dict[str, Any]]], Awaitable[None]]
        ] = None,
    ) -> WriteResult:
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
            memory_config=self.memory_config,
            end_user_id=self.end_user_id,
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
        chunked_dialogs: List[DialogData],
        language: str = "zh",
        progress_callback: Optional[
            Callable[[str, str, Optional[Dict[str, Any]]], Awaitable[None]]
        ] = None,
    ) -> PilotWriteResult:
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
            memory_config=self.memory_config,
            end_user_id=self.end_user_id,
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

    async def reflect(self) -> dict:
        """反思：检测事实冲突并修正"""
        raise NotImplementedError("ReflectionPipeline 尚未实现")

    # async def cluster(self, new_entity_ids: list[str] = None) -> None:
    #     """聚类：全量初始化或增量更新社区"""
    #     raise NotImplementedError("ClusteringPipeline 尚未实现")
