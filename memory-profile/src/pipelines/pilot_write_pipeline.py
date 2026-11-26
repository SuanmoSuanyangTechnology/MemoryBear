"""PilotWritePipeline — 试运行专用萃取流水线。

职责边界：
- 只执行"萃取相关"链路：statement -> triplet -> graph_build -> 第一层去重消歧
- 不负责 Neo4j 写入、聚类、摘要、缓存更新
- Use model clients injected by the calling service

Dependency direction: Service -> Pipeline -> extraction steps.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.storage_services.extraction_engine.extraction_pipeline_orchestrator import (
    NewExtractionOrchestrator,
)
from src.schemas.message_models import DialogData
from src.storage_services.extraction_engine.steps.dedup_step import (
    DedupResult,
    run_dedup,
)
from src.storage_services.extraction_engine.steps.graph_build_step import (
    GraphBuildResult,
    build_graph_nodes_and_edges,
)

if TYPE_CHECKING:
    from src.schemas.memory_config import MemoryConfig

@dataclass
class PilotWriteResult:
    """试运行流水线输出。"""

    dialog_data_list: list[DialogData]
    graph: GraphBuildResult
    dedup: DedupResult

    @property
    def stats(self) -> dict[str, int]:
        return {
            "chunk_count": len(self.graph.chunk_nodes),
            "statement_count": len(self.graph.statement_nodes),
            "entity_count_before_dedup": len(self.graph.entity_nodes),
            "entity_count_after_dedup": len(self.dedup.entity_nodes),
            "relation_count_before_dedup": len(self.graph.entity_entity_edges),
            "relation_count_after_dedup": len(self.dedup.entity_entity_edges),
        }


class PilotWritePipeline:
    """重构后试运行专用流水线。

    The calling service owns model resolution and client lifecycle.
    """

    def __init__(
        self,
        memory_config: MemoryConfig,
        end_user_id: str,
        language: str = "zh",
        progress_callback: Callable[[str, str, dict[str, Any] | None], Awaitable[None]]
        | None = None,
        *,
        llm_client=None,
        embedder_client=None,
    ) -> None:
        """
        Args:
            memory_config: 不可变的记忆配置对象（从数据库加载）
            end_user_id: 终端用户 ID
            language: 语言 ("zh" | "en")
            progress_callback: 可选的进度回调
        """
        self.memory_config = memory_config
        self.end_user_id = end_user_id
        self.language = language
        self.progress_callback = progress_callback

        # Clients are injected; the engine never opens an API database session.
        self._llm_client = llm_client
        self._embedder_client = embedder_client

    async def run(self, dialog_data_list: list[DialogData]) -> PilotWriteResult:
        """执行试运行萃取链路。

        Execute extraction, graph construction and deduplication.
        """
        from src.services.memory_config.service import get_pipeline_config

        if self._llm_client is None:
            raise RuntimeError("Pilot extraction requires an injected model client")
        pipeline_config = get_pipeline_config(self.memory_config)

        orchestrator = NewExtractionOrchestrator(
            llm_client=self._llm_client,
            embedder_client=self._embedder_client,
            config=pipeline_config,
            embedding_id=str(self.memory_config.embedding_model_id),
            language=self.language,
            is_pilot_run=True,
            progress_callback=self.progress_callback,
        )
        extracted_dialogs = await orchestrator.run(dialog_data_list)

        graph = await build_graph_nodes_and_edges(
            dialog_data_list=extracted_dialogs,
            embedder_client=self._embedder_client,
            progress_callback=self.progress_callback,
        )

        dedup = await run_dedup(
            entity_nodes=graph.entity_nodes,
            statement_entity_edges=graph.stmt_entity_edges,
            entity_entity_edges=graph.entity_entity_edges,
            dialog_data_list=extracted_dialogs,
            pipeline_config=pipeline_config,
            connector=None,  # pilot: no layer-2 db dedup
            llm_client=self._llm_client,
            is_pilot_run=True,
            progress_callback=self.progress_callback,
        )

        return PilotWriteResult(
            dialog_data_list=extracted_dialogs,
            graph=graph,
            dedup=dedup,
        )

    # ──────────────────────────────────────────────
    # 辅助方法
    # ──────────────────────────────────────────────
