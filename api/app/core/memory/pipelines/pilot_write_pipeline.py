"""PilotWritePipeline — 试运行专用萃取流水线。

职责边界：
- 只执行“萃取相关”链路：statement -> triplet -> graph_build -> 第一层去重消歧
- 不负责 Neo4j 写入、聚类、摘要、缓存更新
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional

from app.core.memory.models.message_models import DialogData
from app.core.memory.models.variate_config import ExtractionPipelineConfig
from app.core.memory.storage_services.extraction_engine.dedup_step import (
    DedupResult,
    run_dedup,
)
from app.core.memory.storage_services.extraction_engine.steps.extraction_pipeline_orchestrator import (
    NewExtractionOrchestrator,
)
from app.core.memory.storage_services.extraction_engine.steps.graph_build_step import (
    GraphBuildResult,
    build_graph_nodes_and_edges,
)


@dataclass
class PilotWriteResult:
    """试运行流水线输出。"""

    dialog_data_list: List[DialogData]
    graph: GraphBuildResult
    dedup: DedupResult

    @property
    def stats(self) -> Dict[str, int]:
        return {
            "chunk_count": len(self.graph.chunk_nodes),
            "statement_count": len(self.graph.statement_nodes),
            "entity_count_before_dedup": len(self.graph.entity_nodes),
            "entity_count_after_dedup": len(self.dedup.entity_nodes),
            "relation_count_before_dedup": len(self.graph.entity_entity_edges),
            "relation_count_after_dedup": len(self.dedup.entity_entity_edges),
        }


class PilotWritePipeline:
    """重构后试运行专用流水线。"""

    def __init__(
        self,
        llm_client: Any,
        embedder_client: Any,
        pipeline_config: ExtractionPipelineConfig,
        embedding_id: Optional[str],
        language: str = "zh",
        ontology_types: Any = None,
        progress_callback: Optional[
            Callable[[str, str, Optional[Dict[str, Any]]], Awaitable[None]]
        ] = None,
    ) -> None:
        self.llm_client = llm_client
        self.embedder_client = embedder_client
        self.pipeline_config = pipeline_config
        self.embedding_id = embedding_id
        self.language = language
        self.ontology_types = ontology_types
        self.progress_callback = progress_callback

    async def run(self, dialog_data_list: List[DialogData]) -> PilotWriteResult:
        """执行试运行萃取链路。"""
        orchestrator = NewExtractionOrchestrator(
            llm_client=self.llm_client,
            embedder_client=self.embedder_client,
            config=self.pipeline_config,
            embedding_id=self.embedding_id,
            ontology_types=self.ontology_types,
            language=self.language,
            is_pilot_run=True,
            progress_callback=self.progress_callback,
        )
        extracted_dialogs = await orchestrator.run(dialog_data_list)

        graph = await build_graph_nodes_and_edges(
            dialog_data_list=extracted_dialogs,
            embedder_client=self.embedder_client,
            progress_callback=self.progress_callback,
        )

        dedup = await run_dedup(
            entity_nodes=graph.entity_nodes,
            statement_entity_edges=graph.stmt_entity_edges,
            entity_entity_edges=graph.entity_entity_edges,
            dialog_data_list=extracted_dialogs,
            pipeline_config=self.pipeline_config,
            connector=None,  # pilot: no layer-2 db dedup
            llm_client=self.llm_client,
            is_pilot_run=True,
            progress_callback=self.progress_callback,
        )

        return PilotWriteResult(
            dialog_data_list=extracted_dialogs,
            graph=graph,
            dedup=dedup,
        )

