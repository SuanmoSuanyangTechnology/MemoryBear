"""PilotWritePipeline — 试运行专用萃取流水线。

职责边界：
- 只执行"萃取相关"链路：statement -> triplet -> graph_build -> 第一层去重消歧
- 不负责 Neo4j 写入、聚类、摘要、缓存更新
- 自行管理客户端初始化和本体类型加载（与 WritePipeline 对齐）

依赖方向：Facade → Pipeline → Engine → Repository（单向，不允许反向调用）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, List, Optional

from app.core.memory.models.message_models import DialogData
from app.core.memory.storage_services.extraction_engine.steps.dedup_step import (
    DedupResult,
    run_dedup,
)
from app.core.memory.storage_services.extraction_engine.extraction_pipeline_orchestrator import (
    NewExtractionOrchestrator,
)
from app.core.memory.storage_services.extraction_engine.steps.graph_build_step import (
    GraphBuildResult,
    build_graph_nodes_and_edges,
)

if TYPE_CHECKING:
    from app.schemas.memory_config_schema import MemoryConfig

logger = logging.getLogger(__name__)


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
    """重构后试运行专用流水线。

    构造函数只接收 memory_config，客户端初始化和本体加载在 run() 内部完成，
    与 WritePipeline 保持一致的生命周期管理模式。
    """

    def __init__(
        self,
        memory_config: MemoryConfig,
        end_user_id: str,
        language: str = "zh",
        progress_callback: Optional[
            Callable[[str, str, Optional[Dict[str, Any]]], Awaitable[None]]
        ] = None,
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

        # 延迟初始化的客户端
        self._llm_client = None
        self._embedder_client = None

    async def run(self, dialog_data_list: List[DialogData]) -> PilotWriteResult:
        """执行试运行萃取链路。

        内部完成客户端初始化 → 本体加载 → 萃取 → 图构建 → 去重。
        """
        from app.core.memory.utils.config.config_utils import get_pipeline_config

        self._init_clients()
        pipeline_config = get_pipeline_config(self.memory_config)
        ontology_types = self._load_ontology_types()

        orchestrator = NewExtractionOrchestrator(
            llm_client=self._llm_client,
            embedder_client=self._embedder_client,
            config=pipeline_config,
            embedding_id=str(self.memory_config.embedding_model_id),
            ontology_types=ontology_types,
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

    def _init_clients(self) -> None:
        """从 MemoryConfig 构建 LLM 和 Embedding 客户端。"""
        from app.core.memory.utils.llm.llm_utils import MemoryClientFactory
        from app.db import get_db_context

        with get_db_context() as db:
            factory = MemoryClientFactory(db)
            self._llm_client = factory.get_llm_client_from_config(self.memory_config)
            self._embedder_client = factory.get_embedder_client_from_config(
                self.memory_config
            )
        logger.info("Pilot pipeline: LLM and embedding clients constructed")

    def _load_ontology_types(self):
        """加载本体类型配置（如果配置了 scene_id）。"""
        if not self.memory_config.scene_id:
            return None

        try:
            from app.core.memory.ontology_services.ontology_type_loader import (
                load_ontology_types_for_scene,
            )
            from app.db import get_db_context

            with get_db_context() as db:
                ontology_types = load_ontology_types_for_scene(
                    scene_id=self.memory_config.scene_id,
                    workspace_id=self.memory_config.workspace_id,
                    db=db,
                )
            if ontology_types:
                logger.info(
                    f"Loaded {len(ontology_types.types)} ontology types "
                    f"for scene_id: {self.memory_config.scene_id}"
                )
            return ontology_types
        except Exception as e:
            logger.warning(
                f"Failed to load ontology types for scene_id "
                f"{self.memory_config.scene_id}: {e}",
                exc_info=True,
            )
            return None
