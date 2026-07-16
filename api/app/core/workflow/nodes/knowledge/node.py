import json
import logging
import re
from typing import Any

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.rag.retrieval.async_models import AsyncRetrievalModelGateway
from app.core.rag.retrieval.models import ModelRuntimeSnapshot
from app.core.workflow.engine.state_manager import WorkflowState
from app.core.workflow.engine.variable_pool import VariablePool
from app.core.workflow.nodes.base_node import BaseNode
from app.core.workflow.nodes.knowledge import KnowledgeRetrievalNodeConfig
from app.core.workflow.nodes.llm.config import strip_unsupported_llm_params
from app.core.workflow.variable.base_variable import VariableType
from app.db import get_async_db_context
from app.schemas.chunk_schema import KnowledgeRetrievalCaller, RetrieveType
from app.models.models_model import ModelCapability
from app.schemas.knowledge_metadata_schema import FilterCondition, FilterGroup, MetadataFilterMode
from app.schemas.knowledge_retrieval_schema import KnowledgeRetrievalRequest
from app.services.knowledge_metadata_service import KnowledgeMetadataService
from app.services.knowledge_retrieval_preparation import KnowledgeRetrievalPreparation
from app.services.knowledge_retrieval_service import KnowledgeRetrievalService
from app.services.model_service import ModelConfigService

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

    def _is_cache_enabled(self) -> bool:
        # auto 模式：过滤条件由 LLM 在 execute 内部动态提取，不进 cache key（_extract_input
        # 阶段 LLM 尚未运行，无法把提取结果纳入 key）。因此同一 query 的 cache key 恒定，
        # 二次执行会命中旧缓存、跳过 LLM 提取并返回过时结果。故 auto 模式默认不走节点缓存；
        # 显式配置 cache.enabled=true 时仍尊重（交由父类判断）。
        if (
            self._get_typed_config().metadata_filter_mode == MetadataFilterMode.AUTO
            and not self.cache_config.get("enabled")
        ):
            logger.debug(
                "node: %s metadata_filter_mode=auto, bypass node cache (LLM dynamic extraction)",
                self.node_id,
            )
            return False
        return super()._is_cache_enabled()

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

    async def _prepare_auto_filter_state_async(
        self,
    ) -> tuple[dict[str, Any], ModelRuntimeSnapshot, dict[str, Any]] | None:
        """Snapshot the Workflow AUTO filter inputs in a short async DB context."""
        cfg = self._get_typed_config()
        async with get_async_db_context() as db:
            metadata_defs_by_kb = {
                kb.kb_id: await KnowledgeMetadataService.get_metadata_defs_for_filtering_async(
                    db,
                    kb.kb_id,
                )
                for kb in cfg.knowledge_bases
            }
            common_metadata_defs = KnowledgeRetrievalPreparation._get_common_metadata_defs(
                metadata_defs_by_kb,
            )
            if not common_metadata_defs:
                logger.info(
                    "node: %s auto filter skipped because no common metadata fields exist",
                    self.node_id,
                )
                return None

            model_cfg = cfg.metadata_model
            if not model_cfg or not model_cfg.model_id:
                raise BusinessException(
                    "auto 模式必须配置 metadata_model.model_id",
                    code=BizCode.INVALID_PARAMETER,
                )
            model_config = await ModelConfigService.get_model_by_id_async(
                db,
                model_cfg.model_id,
            )
            api_key = self.model_balance(model_config)
            model = ModelRuntimeSnapshot(
                model_name=api_key.model_name,
                provider=api_key.provider or model_config.provider,
                api_key=api_key.api_key,
                api_base=api_key.api_base,
                capability=tuple(api_key.capability or model_config.capability or ()),
                is_omni=(
                    api_key.is_omni
                    if api_key.is_omni is not None
                    else bool(model_config.is_omni)
                ),
                model_type=model_config.type,
            )

        return (
            common_metadata_defs,
            model,
            self._build_auto_filter_generation_options(model),
        )

    def _build_auto_filter_generation_options(
        self,
        model: ModelRuntimeSnapshot,
    ) -> dict[str, Any]:
        """Normalize Workflow completion parameters for the native metadata adapter."""
        params = self._get_typed_config().metadata_model.completion_params
        options: dict[str, Any] = {}
        if params.temperature is not None:
            options["temperature"] = params.temperature
        if params.max_tokens is not None:
            options["max_tokens"] = params.max_tokens
        if params.top_p.enable and params.top_p.value is not None:
            options["top_p"] = params.top_p.value
        if params.top_k.enable and params.top_k.value is not None:
            options["top_k"] = params.top_k.value
        if params.seed.enable and params.seed.value is not None:
            options["seed"] = params.seed.value
        if params.repetition_penalty.enable and params.repetition_penalty.value is not None:
            options["repetition_penalty"] = params.repetition_penalty.value
        if params.frequency_penalty.enable and params.frequency_penalty.value is not None:
            options["frequency_penalty"] = params.frequency_penalty.value
        if params.presence_penalty.enable and params.presence_penalty.value is not None:
            options["presence_penalty"] = params.presence_penalty.value
        if params.stop.enable and params.stop.value:
            options["stop"] = params.stop.value[:4]
        if params.search:
            options["enable_search"] = True
        if params.thinking.enable:
            options["deep_thinking"] = True
            if params.thinking.budget.enable and params.thinking.budget.value is not None:
                options["thinking_budget_tokens"] = params.thinking.budget.value
        if (
            (params.json_output or (
                params.response_format.enable
                and params.response_format.value == "json_object"
            ))
            and ModelCapability.JSON_OUTPUT in set(model.capability)
            and not (
                params.thinking.enable
                and ModelCapability.THINKING in set(model.capability)
            )
        ):
            options["response_format"] = {"type": "json_object"}
        if params.extra_headers.enable and params.extra_headers.value:
            try:
                decoded_headers = json.loads(params.extra_headers.value)
            except (TypeError, ValueError):
                logger.warning(
                    "node: %s auto filter ignored invalid extra headers JSON",
                    self.node_id,
                )
            else:
                if isinstance(decoded_headers, dict):
                    options["default_headers"] = decoded_headers
                else:
                    logger.warning(
                        "node: %s auto filter ignored non-object extra headers",
                        self.node_id,
                    )

        options, strip_warnings = strip_unsupported_llm_params(
            options,
            model.provider,
            model.is_omni,
        )
        for warning in strip_warnings:
            logger.warning(
                "node: %s auto filter parameter stripped: %s",
                self.node_id,
                warning,
            )
        return options

    async def _extract_auto_filter_groups_async(self, query: str) -> list[FilterGroup]:
        prepared = await self._prepare_auto_filter_state_async()
        if prepared is None:
            return []

        common_metadata_defs, model, generation_options = prepared
        filter_groups = await AsyncRetrievalModelGateway().generate_metadata_filters(
            query=query,
            metadata_defs=common_metadata_defs,
            model=model,
            generation_options=generation_options,
        )
        return [
            FilterGroup(
                conditions=[
                    FilterCondition(field=condition.field, operator=condition.operator, value=condition.value)
                    for condition in (group.conditions or [])
                ],
                logic=group.logic,
            )
            for group in (filter_groups or [])
        ]

    async def execute(self, state: WorkflowState, variable_pool: VariablePool) -> Any:
        """
        Execute the knowledge retrieval workflow node.

        Delegates all retrieval and metadata filtering to the unified
        KnowledgeRetrievalService.retrieve_async entry point, as specified in
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

        # 2.5 auto 模式：节点层用配置好的模型 + 参数，提取出源数据过滤条件（list[FilterGroup]，配置层类型）
        auto_filter_groups: list | None = None
        if self.typed_config.metadata_filter_mode == MetadataFilterMode.AUTO:
            auto_filter_groups = await self._extract_auto_filter_groups_async(query)

        # 3. Construct KnowledgeRetrievalRequest
        first_kb = self.typed_config.knowledge_bases[0]
        kb_ids = [kb.kb_id for kb in self.typed_config.knowledge_bases]

        # 分词检索不使用 vector_similarity_weight，其他检索类型从配置读取
        if first_kb.retrieve_type == RetrieveType.PARTICIPLE:
            vector_similarity_weight = None
        else:
            vector_similarity_weight = first_kb.vector_similarity_weight
        
        request = KnowledgeRetrievalRequest(
            query=query,
            caller=KnowledgeRetrievalCaller.WORKFLOW,
            kb_ids=kb_ids,
            knowledge_bases=self.typed_config.knowledge_bases,
            similarity_threshold=first_kb.similarity_threshold,
            vector_similarity_weight=vector_similarity_weight,
            top_k=self.typed_config.reranker_top_k or first_kb.top_k,
            retrieve_type=first_kb.retrieve_type,
            rerank_id=self.typed_config.reranker_id,
            metadata_filter_mode=self.typed_config.metadata_filter_mode,
            metadata_filters=(
                auto_filter_groups
                if self.typed_config.metadata_filter_mode == MetadataFilterMode.AUTO
                else ([rendered_filters] if rendered_filters else [])
            ),
        )
        if self.typed_config.metadata_filter_mode == MetadataFilterMode.AUTO:
            request.mark_metadata_filters_prepared()

        # 4. Call unified retrieval service
        result = await KnowledgeRetrievalService.retrieve_async(
            request=request,
            principal=None,
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
