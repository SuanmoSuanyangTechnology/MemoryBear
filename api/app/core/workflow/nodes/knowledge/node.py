import json
import logging
import re
from typing import Any

from sqlalchemy.orm import Session

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.models import RedBearLLM, RedBearModelConfig
from app.core.workflow.engine.state_manager import WorkflowState
from app.core.workflow.engine.variable_pool import VariablePool
from app.core.workflow.nodes.base_node import BaseNode
from app.core.workflow.nodes.knowledge import KnowledgeRetrievalNodeConfig
from app.core.workflow.nodes.llm.config import strip_unsupported_llm_params
from app.core.workflow.variable.base_variable import VariableType
from app.schemas.chunk_schema import KnowledgeRetrievalCaller, RetrieveType
from app.models import ModelType
from app.models.models_model import ModelCapability
from app.schemas.knowledge_metadata_schema import FilterCondition, FilterGroup, MetadataFilterMode
from app.schemas.knowledge_retrieval_schema import KnowledgeRetrievalRequest
from app.schemas.model_schema import ModelInfo
from app.services.knowledge_metadata_service import KnowledgeMetadataService
from app.services.knowledge_retrieval_service import KnowledgeRetrievalService
from app.services.metadata_auto_filter_service import MetadataAutoFilterService
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

    def _build_auto_filter_llm(self, db) -> RedBearLLM:
        """auto 模式：照搬 LLM 节点 _prepare_llm 的构造方式，用 RedBearLLM + extra_params。

        参数折叠进 RedBearModelConfig.extra_params，经 strip_unsupported_llm_params 按 provider
        剔除不支持的键并打 warning；stop 等经 RedBearModelFactory.get_model_params 路由到顶层，
        provider 真正认（旧 chat_model.Base 路径下 stop 不生效的根因即在此）。与
        MetadataAutoFilterService.generate_filter_groups 期望的 llm.invoke() 接口一致。

        api-key 选取沿用旧 chat_model.Base 路径的 model_balance（取 config.api_keys[0]，直接用
        其 api_base），不切到 get_runtime_api_config：后者对 SpeedBear 公共模型会走
        _build_speedbear_runtime_api_key，base_url 指向 SPEEDBEAR_BASE_URL 网关，本地开发网络下
        不可达会触发 Connection error。stop 修复只依赖 RedBearLLM 构造，与 api-key 来源无关。
        """
        model_cfg = self.typed_config.metadata_model
        config = ModelConfigService.get_model_by_id(db=db, model_id=model_cfg.model_id)
        if not config:
            raise BusinessException(
                "auto 模式配置的模型不存在", code=BizCode.NOT_FOUND
            )
        api_key = self.model_balance(config)
        model_info = ModelInfo(
            model_name=api_key.model_name,
            model_type=ModelType(config.type),
            api_key=api_key.api_key,
            api_base=api_key.api_base,
            provider=api_key.provider or config.provider,
            is_omni=api_key.is_omni if api_key.is_omni is not None else config.is_omni,
            capability=api_key.capability or config.capability or [],
        )

        p = model_cfg.completion_params
        extra_params: dict[str, Any] = {}
        # enable 门与 LLM 节点完全一致：temperature/max_tokens 无 enable 门，其余需 enable=true
        if p.temperature is not None:
            extra_params["temperature"] = p.temperature
        if p.max_tokens is not None:
            extra_params["max_tokens"] = p.max_tokens
        if p.top_p.enable and p.top_p.value is not None:
            extra_params["top_p"] = p.top_p.value
        if p.top_k.enable and p.top_k.value is not None:
            extra_params["top_k"] = p.top_k.value
        if p.seed.enable and p.seed.value is not None:
            extra_params["seed"] = p.seed.value
        if p.repetition_penalty.enable and p.repetition_penalty.value is not None:
            extra_params["repetition_penalty"] = p.repetition_penalty.value
        if p.frequency_penalty.enable and p.frequency_penalty.value is not None:
            extra_params["frequency_penalty"] = p.frequency_penalty.value
        if p.presence_penalty.enable and p.presence_penalty.value is not None:
            extra_params["presence_penalty"] = p.presence_penalty.value
        if p.stop.enable and p.stop.value:
            extra_params["stop"] = p.stop.value[:4]
        if p.search:
            extra_params["enable_search"] = True

        deep_thinking = p.thinking.enable
        thinking_budget_tokens = p.thinking.budget.value if (
            p.thinking.budget.enable and p.thinking.budget.value is not None
        ) else None

        capability_set = set(model_info.capability or [])
        json_output = bool(p.json_output)
        if (
            p.response_format.enable
            and p.response_format.value == "json_object"
            and ModelCapability.JSON_OUTPUT in capability_set
        ):
            extra_params["response_format"] = {"type": "json_object"}

        if p.extra_headers.enable and p.extra_headers.value:
            try:
                extra_params["default_headers"] = json.loads(p.extra_headers.value)
            except (ValueError, TypeError) as e:
                logger.warning(
                    f"node: {self.node_id} auto filter: extra_headers JSON parse failed: {e}"
                )

        # 按 provider 剔除不支持的参数并打 warning（与 LLM 节点 strip_unsupported_llm_params 一致）
        extra_params, strip_warnings = strip_unsupported_llm_params(
            extra_params, model_info.provider or "", model_info.is_omni
        )
        for w in strip_warnings:
            logger.warning(
                f"节点 {self.node_id} auto filter 参数安全剥离: {w} "
                f"(模型={model_info.model_name}, 提供商={model_info.provider})"
            )

        logger.info(
            f"节点 {self.node_id} auto filter: provider={model_info.provider}, "
            f"model={model_info.model_name}, is_omni={model_info.is_omni}, "
            f"is_public={getattr(config, 'is_public', None)}, "
            f"base_url={model_info.api_base!r}, api_key_set={bool(model_info.api_key)}, "
            f"extra_params={extra_params}"
        )

        return RedBearLLM(
            RedBearModelConfig(
                model_name=model_info.model_name,
                provider=model_info.provider,
                api_key=model_info.api_key,
                base_url=model_info.api_base,
                is_omni=model_info.is_omni,
                capability=model_info.capability,
                deep_thinking=deep_thinking,
                thinking_budget_tokens=thinking_budget_tokens,
                json_output=json_output,
                extra_params=extra_params,
            ),
            type=model_info.model_type,
        )

    def _prepare_auto_filter_state_sync(
        self,
        db: Session,
    ) -> tuple[dict[str, Any], RedBearLLM] | None:
        cfg = self._get_typed_config()
        metadata_defs_by_kb = {
            kb.kb_id: KnowledgeMetadataService.get_metadata_defs_for_filtering(db, kb.kb_id)
            for kb in cfg.knowledge_bases
        }
        common_metadata_defs = KnowledgeRetrievalService._get_common_metadata_defs(metadata_defs_by_kb)

        if not common_metadata_defs:
            logger.info(
                "node: %s auto filter: no common metadata fields, skip extraction",
                self.node_id,
            )
            return None

        return common_metadata_defs, self._build_auto_filter_llm(db)

    def _extract_auto_filter_groups(
        self,
        query: str,
        common_metadata_defs: dict[str, Any],
        llm: RedBearLLM,
    ) -> list:
        """auto 模式：用配置好的模型 + 参数，调用 LLM 提取出源数据过滤条件。

        产出 list[FilterGroup]（配置层类型），直接放进 metadata_filters 交给 service；
        service 再经 _build_common_filter_groups 转成引擎层类型做真正的过滤。
        """
        cfg = self._get_typed_config()
        model_cfg = cfg.metadata_model
        if not model_cfg or not model_cfg.model_id:
            raise BusinessException(
                "auto 模式必须配置 metadata_model.model_id",
                code=BizCode.INVALID_PARAMETER,
            )

        # 3. 调用提取方法（参数已在 llm 实例上，不再单独传 gen_conf）
        logger.info(
            "node: %s auto filter: query=%r, fields=%s",
            self.node_id, query, list(common_metadata_defs.keys()),
        )
        filter_groups = MetadataAutoFilterService.generate_filter_groups(
            query=query,
            metadata_defs=common_metadata_defs,
            llm=llm,
        )
        # generate_filter_groups 返回引擎层 EngineFilterGroup；metadata_filters 字段装的是配置层 FilterGroup，
        # 这里无损转成配置层类型（引擎层 logic 大写，由 config 校验器统一转小写）。
        filter_groups = [
            FilterGroup(
                conditions=[
                    FilterCondition(field=c.field, operator=c.operator, value=c.value)
                    for c in (g.conditions or [])
                ],
                logic=g.logic,
            )
            for g in (filter_groups or [])
        ]
        # 打印提取出的过滤条件（即交接给知识库工程师做真正过滤的内容）
        logger.info(
            "node: %s auto filter extracted: %s",
            self.node_id,
            [
                {
                    "logic": group.logic,
                    "conditions": [cond.model_dump() for cond in (group.conditions or [])],
                }
                for group in (filter_groups or [])
            ],
        )
        return filter_groups

    async def _extract_auto_filter_groups_async(self, query: str) -> list:
        del query
        logger.info(
            "node: %s auto filter is delegated to native knowledge retrieval",
            self.node_id,
        )
        return []

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
            metadata_filters=auto_filter_groups or ([rendered_filters] if rendered_filters else []),
        )

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
