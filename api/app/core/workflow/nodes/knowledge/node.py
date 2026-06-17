import asyncio
import logging
import re
from typing import Any

from app.core.workflow.engine.state_manager import WorkflowState
from app.core.workflow.engine.variable_pool import VariablePool
from app.core.workflow.nodes.base_node import BaseNode
from app.core.workflow.nodes.knowledge import KnowledgeRetrievalNodeConfig
from app.core.workflow.variable.base_variable import VariableType
from app.db import get_db_read
from app.schemas.knowledge_metadata_schema import FilterCondition, FilterGroup, MetadataFilterMode
from app.schemas.knowledge_retrieval_schema import KnowledgeRetrievalRequest
from app.services.knowledge_retrieval_service import KnowledgeRetrievalService

logger = logging.getLogger(__name__)

# 匹配"纯变量引用"，如 {{node.x.output}} / {{ sys.message }}（无其它文本）。
# 用于把数组/数字等变量解析为原生值，而不是被 Jinja2 str 化成 Python repr（如 ['22222']）。
_PURE_VARIABLE_PATTERN = re.compile(r"^\{\{\s*(.*?)\s*\}\}$", re.DOTALL)


class KnowledgeRetrievalNode(BaseNode):
    def __init__(self, node_config: dict[str, Any], workflow_config: dict[str, Any], down_stream_nodes: list[str]):
        super().__init__(node_config, workflow_config, down_stream_nodes)
        self.typed_config: KnowledgeRetrievalNodeConfig | None = None

    def _get_typed_config(self) -> KnowledgeRetrievalNodeConfig:
        if self.typed_config is None:
            self.typed_config = KnowledgeRetrievalNodeConfig(**self.config)
        return self.typed_config

    def _output_types(self) -> dict[str, VariableType]:
        return {
            "output": VariableType.ARRAY_STRING
        }

    def _extract_output(self, business_result: Any) -> Any:
        """下游节点只拿 chunks 列表"""
        if isinstance(business_result, dict) and "chunks" in business_result:
            return business_result["chunks"]
        return business_result

    @staticmethod
    def _extract_citations(business_result: Any) -> list:
        if isinstance(business_result, dict):
            return business_result.get("citations", [])
        return []

    def _extract_extra_fields(self, business_result: Any) -> dict:
        citations = self._extract_citations(business_result)
        process: dict = {"citations": citations}
        if isinstance(business_result, dict):
            process["chunks_count"] = len(business_result.get("chunks", []))
            mf = business_result.get("_metadata_filter_result")
            if isinstance(mf, dict):
                process["metadata_filter"] = {
                    "mode": mf.get("mode"),
                    "status": mf.get("status"),
                    "reason": mf.get("reason"),
                    "hit_count": mf.get("hit_count"),
                    "condition_count": mf.get("condition_count"),
                    "elapsed_ms": mf.get("elapsed_ms"),
                    "skipped_fields": mf.get("skipped_fields"),
                }
        return {"citations": citations, "process": process}

    def _extract_input(self, state: WorkflowState, variable_pool: VariablePool) -> dict[str, Any]:
        cfg = self._get_typed_config()
        # 复用 execute() 中的渲染逻辑，保证 input 记录的是变量解析后的真实值，
        # 而非原始模板 {{xxx}}，与 assigner / LLM 等节点的展示约定保持一致。
        rendered_filters = self._render_filter_variables(cfg.metadata_filters, variable_pool)
        return {
            "query": self._render_template(cfg.query, variable_pool),
            "knowledge_bases": [kb_config.model_dump(mode="json") for kb_config in cfg.knowledge_bases],
            "metadata_filter_mode": cfg.metadata_filter_mode.value,
            "metadata_filters": rendered_filters and {
                "logic": rendered_filters.logic.value,
                "conditions": [{"field": c.field, "operator": c.operator, "value": c.value, "value_type": c.value_type} for c in rendered_filters.conditions],
            },
        }

    def _render_filter_variables(
        self,
        filter_group: FilterGroup | None,
        variable_pool: VariablePool,
    ) -> FilterGroup | None:
        """渲染 metadata_filters 中 value_type=variable 的条件值。

        遍历 FilterGroup，对 value_type 为 'variable' 且值含 {{...}} 的条件：
          - 纯变量引用（如 {{node.x.output}}）：直接取变量池中的原生值，
            保留 list/number/dict 等结构，避免 Jinja2 把数组 str 化成 Python repr（如 ['22222']）。
            这样数组既能在 input 里显示为 JSON 数组，也能让 in/not_in 等操作符正确工作。
          - 混合模板（如 "前缀 {{x}}"）：退回到 _render_template 字符串渲染。
        """
        if not filter_group:
            return None

        rendered_conditions = []
        for condition in filter_group.conditions:
            value = condition.value
            if condition.value_type == "variable" and isinstance(value, str) and "{{" in value:
                pure_ref = _PURE_VARIABLE_PATTERN.match(value.strip())
                if pure_ref and variable_pool.has(value.strip()):
                    # 纯变量引用：保留原生结构（list/number/...）
                    value = variable_pool.get_value(value.strip(), default=value, strict=False)
                else:
                    # 混合模板或变量不存在：字符串渲染
                    value = self._render_template(value, variable_pool, strict=False)
            rendered_conditions.append(FilterCondition(
                field=condition.field,
                operator=condition.operator,
                value=value,
                value_type=condition.value_type,
            ))

        return FilterGroup(conditions=rendered_conditions, logic=filter_group.logic)

    @staticmethod
    def _build_citations(chunks: list[Any]) -> list[dict]:
        """从 chunks 的 metadata 中提取 citations 信息"""
        citations = []
        seen_doc_ids = set()
        for chunk in chunks:
            meta = chunk.metadata if hasattr(chunk, "metadata") else {}
            document_id = meta.get("document_id")
            if document_id and document_id not in seen_doc_ids:
                seen_doc_ids.add(document_id)
                citations.append({
                    "document_id": str(document_id),
                    "doc_id": meta.get("doc_id", ""),
                    "file_name": meta.get("file_name", ""),
                    "knowledge_id": str(meta.get("knowledge_id", "")),
                    "score": meta.get("score", 0.0),
                })
        return citations

    async def execute(self, state: WorkflowState, variable_pool: VariablePool) -> Any:
        """
        Execute the knowledge retrieval workflow node.

        Delegates all retrieval and metadata filtering to the unified
        KnowledgeRetrievalService.retrieve entry point, as specified in
        the knowledge retrieval API convention document.

        Args:
            state (WorkflowState): Current workflow execution state.
            variable_pool: Variable Pool

        Returns:
            dict: {chunks, citations, _metadata_filter_result}
        """
        self.typed_config = self._get_typed_config()
        if not self.typed_config.knowledge_bases:
            return {
                "chunks": [],
                "citations": [],
                "_metadata_filter_result": {"mode": "disabled", "status": "skipped"},
            }

        # 1. Render query template
        query = self._render_template(self.typed_config.query, variable_pool)

        # 2. Pre-render variable templates in metadata filter conditions
        rendered_filters = self._render_filter_variables(
            self.typed_config.metadata_filters, variable_pool
        )

        # 3. Construct KnowledgeRetrievalRequest
        #    Use first KB's config as global defaults (user confirmed: accept global params)
        first_kb = self.typed_config.knowledge_bases[0]
        kb_ids = [kb.kb_id for kb in self.typed_config.knowledge_bases]

        request = KnowledgeRetrievalRequest(
            query=query,
            kb_ids=kb_ids,
            similarity_threshold=first_kb.similarity_threshold,
            vector_similarity_weight=first_kb.vector_similarity_weight,
            top_k=first_kb.top_k,
            retrieve_type=first_kb.retrieve_type,
            rerank_id=self.typed_config.reranker_id,
            metadata_filter_mode=self.typed_config.metadata_filter_mode,
            metadata_filters=[rendered_filters] if rendered_filters else [],
        )

        # 4. Call unified retrieval service
        with get_db_read() as db:
            result = await asyncio.to_thread(
                KnowledgeRetrievalService.retrieve,
                db=db,
                request=request,
                current_user=None,  # workflow nodes have no user context
            )

        # 5. Assemble return format
        chunks = result.chunks
        citations = self._build_citations(chunks)

        mf_status = "applied" if chunks else "applied_empty"
        if self.typed_config.metadata_filter_mode == MetadataFilterMode.DISABLED:
            mf_status = "skipped"

        return {
            "chunks": [chunk.page_content if hasattr(chunk, "page_content") else str(chunk) for chunk in chunks],
            "citations": citations,
            "_metadata_filter_result": {
                "mode": self.typed_config.metadata_filter_mode.value,
                "status": mf_status,
            },
        }