import json
import logging
import re
import uuid
from typing import Any

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.models import RedBearLLM, RedBearModelConfig
from app.core.rag.retrieval.models import ModelRuntimeSnapshot
from app.core.workflow.engine.state_manager import WorkflowState
from app.core.workflow.engine.variable_pool import VariablePool
from app.core.workflow.nodes.base_node import BaseNode
from app.core.workflow.nodes.knowledge import KnowledgeRetrievalNodeConfig
from app.core.workflow.nodes.llm.config import strip_unsupported_llm_params
from app.core.workflow.variable.base_variable import VariableType
from app.db import get_async_db_context
from app.integrations.knowledge.context_factory import build_app_knowledge_context
from app.integrations.knowledge.contracts import KnowledgeRetrievalSource
from app.integrations.knowledge.runtime import get_knowledge_retriever
from app.schemas.chunk_schema import RetrieveType
from app.models.models_model import LLM_FAMILY_TYPES, ModelFeature, ModelType
from app.schemas.knowledge_metadata_schema import FilterCondition, FilterGroup, MetadataFilterMode
from app.schemas.knowledge_retrieval_schema import KnowledgeRetrievalRequest
from app.services.file_content_service import FileReference, resolve_image_retrieval_query
from app.services.knowledge_metadata_service import KnowledgeMetadataService
from app.services.knowledge_retrieval_preparation import KnowledgeRetrievalPreparation
from app.services.metadata_auto_filter_service import MetadataAutoFilterService
from app.services.model_service import ModelApiKeyService, ModelConfigService

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

    def _resolve_image_reference(self, variable_pool: VariablePool) -> FileReference | None:
        """解析 image_query 变量引用为首张可用图片引用。

        仅支持纯变量引用（``{{...}}``），类型为 file 或 array[file]；数组取第一张
        图片。「可用」= 带 file_id（本地文件，由本服务自取字节）或带 url（远程图片）。
        变量不存在、非纯引用、无图片时返回 None。
        """
        image_template = (self._get_typed_config().image_query or "").strip()
        if not image_template:
            return None
        pure_ref = _PURE_VARIABLE_PATTERN.match(image_template)
        if not pure_ref:
            logger.warning(
                "knowledge node image_query 非纯变量引用（必须形如 {{node.x.images}}）: %r",
                image_template,
            )
            return None
        if not variable_pool.has(image_template):
            logger.warning(
                "knowledge node image_query 变量在变量池中不存在: %r", image_template,
            )
            return None
        value = variable_pool.get_value(image_template, strict=False)
        candidates = value if isinstance(value, list) else [value]
        logger.info(
            "knowledge node image_query 解析 template=%r 候选数=%d 样本=%r",
            image_template,
            len(candidates),
            candidates[0] if candidates else None,
        )
        for candidate in candidates:
            reference = FileReference.from_payload(candidate)
            # 本地文件（transfer_method=local_file）只有 file_id、url 为空，
            # 不能像旧实现那样只认 url，否则内网上传的图片永远解析不到。
            if reference is not None and reference.is_image and (reference.file_id or reference.url):
                return reference
        logger.warning(
            "knowledge node image_query 变量中未找到可用图片项（需带 file_id 或 url）: %r value=%r",
            image_template, value,
        )
        return None

    def _extract_input(self, state: WorkflowState, variable_pool: VariablePool) -> dict[str, Any]:
        cfg = self._get_typed_config()
        rendered_filters = self._render_filter_variables(cfg.metadata_filters, variable_pool)
        image_reference = self._resolve_image_reference(variable_pool)
        return {
            # 二选一：image_query 命中图片时记录图片模态（只放短标识，避免 base64
            # 进审计日志与缓存键），否则记录文本 query 渲染值
            "query": {"modality": "image", "content": image_reference.locator} if image_reference
            else self._render_template(cfg.query, variable_pool),
            "image_query": cfg.image_query,
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
        variable_pool: VariablePool,
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
            api_key = await ModelApiKeyService.get_available_api_key_async(
                db,
                model_config.id,
                tenant_id=await self.resolve_tenant_id_async(variable_pool),
            )
            if not api_key:
                raise BusinessException("模型配置缺少 API Key", BizCode.INVALID_PARAMETER)
            model = ModelRuntimeSnapshot(
                model_name=api_key.model_name,
                provider=api_key.provider or model_config.provider,
                api_key=api_key.api_key,
                api_base=api_key.api_base,
                input_modalities=tuple(api_key.input_modalities or ()),
                output_modalities=tuple(api_key.output_modalities or ()),
                features=tuple(api_key.features or ()),
                model_type=model_config.type,
                tenant_id=api_key.tenant_id,
                model_config_id=api_key.model_config_id,
                channel_id=api_key.channel_id,
                failover_plan=getattr(api_key, "failover_plan", None),
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
            and ModelFeature.JSON_OUTPUT in set(model.features)
            and not (
                params.thinking.enable
                and ModelFeature.THINKING in set(model.features)
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
        )
        for warning in strip_warnings:
            logger.warning(
                "node: %s auto filter parameter stripped: %s",
                self.node_id,
                warning,
            )
        return options

    async def _extract_auto_filter_groups_async(self, query: str, variable_pool: VariablePool) -> list[FilterGroup]:
        prepared = await self._prepare_auto_filter_state_async(variable_pool)
        if prepared is None:
            return []

        common_metadata_defs, model, generation_options = prepared
        model_type = ModelType.LLM
        if str(model.model_type) in LLM_FAMILY_TYPES:
            model_type = ModelType(model.model_type)
        llm = RedBearLLM(
            RedBearModelConfig.from_api_key(model, extra_params=generation_options),
            type=model_type,
        )
        filter_groups = await MetadataAutoFilterService.generate_filter_groups_async(
            query=query,
            metadata_defs=common_metadata_defs,
            llm=llm,
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

        Delegates retrieval through the configured knowledge adapter while
        preserving the unified retrieval request contract.

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

        # 1. query 与 image_query 二选一：image_query 解析到图片时走图片检索
        image_reference = self._resolve_image_reference(variable_pool)
        query = "" if image_reference else self._render_template(self.typed_config.query, variable_pool)

        # image_query 已配置但运行期未解析到图片（变量不存在/为空/非图片），且文本 query 也为空：
        # 明确报错，避免用空 query 构造请求触发难懂的 pydantic 校验错误
        if not image_reference and not (query or "").strip():
            image_template = (self.typed_config.image_query or "").strip()
            if image_template:
                raise BusinessException(
                    f"image_query 未解析到可用图片：{image_template}。"
                    "请确认上游已传入文件/图片变量（如 sys.files），且数组中包含图片"
                    "（本地文件需带 file_id，远程图片需带可访问的 url）。",
                    BizCode.INVALID_PARAMETER,
                )

        # 2. Pre-render variable templates in metadata filter conditions
        rendered_filters = self._render_filter_variables(
            self.typed_config.metadata_filters, variable_pool
        )

        # 3. 解析检索公共参数
        first_kb = self.typed_config.knowledge_bases[0]
        kb_ids = [kb.kb_id for kb in self.typed_config.knowledge_bases]

        # 分词检索不使用 vector_similarity_weight，其他检索类型从配置读取
        if first_kb.retrieve_type == RetrieveType.PARTICIPLE:
            vector_similarity_weight = None
        else:
            vector_similarity_weight = first_kb.vector_similarity_weight

        # 混合检索下是否叠加图谱检索路由：请求级取第一个 KB 的配置作为兜底，
        # 每个 KB 显式配置的 enable_graph_retrieval 仍会在检索层按 KB 覆盖生效
        enable_graph_retrieval = (
            1
            if first_kb.retrieve_type == RetrieveType.HYBRID and first_kb.enable_graph_retrieval
            else 0
        )

        # 4. Resolve the application owner before calling the selected adapter.
        context = await build_app_knowledge_context(
            self.workflow_config.get("app_id"),
            source=KnowledgeRetrievalSource.WORKFLOW,
            trace_id=uuid.uuid4().hex,
        )
        retriever = get_knowledge_retriever()

        # 5. 确定最终 query 与元数据过滤。请求模型要求 query 非空（strip 后），
        #    因此图片模式必须先把图片编码成 data URI（非空）再构造请求，不能用空白占位。
        metadata_filters: list = []
        if image_reference is not None:
            from app.integrations.knowledge.retrieval_policy import image_retrieval_supported
            from app.services.image_retrieval_guard import (
                ensure_image_retrieval_supported,
                expand_knowledge_to_leaf_ids,
            )

            # 文件夹型知识库先展开到叶子库：图片能力取决于叶子库自身绑定的模型。
            leaf_kb_ids = await expand_knowledge_to_leaf_ids(kb_ids, db=None)

            # 先按知识库侧的图片检索边界做本地前置校验：命中时给出与下游一致的
            # 具体原因（检索模式 / 图谱召回 / 自动元数据筛选 / 向量与重排模型），
            # 避免用户只看到一句笼统的"不支持图片检索"。
            await ensure_image_retrieval_supported(
                kb_ids=leaf_kb_ids,
                knowledge_bases=self.typed_config.knowledge_bases,
                retrieve_type=first_kb.retrieve_type,
                rerank_id=self.typed_config.reranker_id,
                rerank_mode=self.typed_config.rerank_mode,
                enable_graph_retrieval=enable_graph_retrieval,
                metadata_filter_mode=self.typed_config.metadata_filter_mode,
            )

            # 兜底：再向知识库侧确认该检索模式/接口支持图片（模型记录变更、多 KB
            # 组合等本地判不出的情形在这里拦截）。
            if not await image_retrieval_supported(
                retriever,
                kb_ids=[str(kb_id) for kb_id in leaf_kb_ids],
                retrieve_type=first_kb.retrieve_type,
                context=context,
                rerank_id=str(self.typed_config.reranker_id) if self.typed_config.reranker_id else None,
            ):
                raise BusinessException(
                    "当前知识库/检索模式不支持图片检索，请改用文本 query 或更换支持多模态检索的知识库",
                    BizCode.INVALID_PARAMETER,
                )
            # 图片检索没有文本 query，跳过基于 LLM 的自动/元数据过滤。
            # 本地文件（私有化环境的内网上传）由本服务直读存储字节后编码，不依赖 URL 可达；
            # 远程图片仍走知识库集成层的下载 + 编码。
            final_query = await resolve_image_retrieval_query(
                image_reference,
                workspace_id=context.principal.workspace_id if context.principal else None,
                tenant_id=context.principal.tenant_id if context.principal else None,
            )
            if final_query is None:
                raise BusinessException(
                    "图片检索失败：无法读取或编码 image_query 指向的图片"
                    "（本地文件请确认上传已完成，远程图片请确认可正常下载）",
                    BizCode.INVALID_PARAMETER,
                )
        else:
            final_query = query
            # auto 模式：节点层用配置好的模型 + 参数提取源数据过滤条件（list[FilterGroup]）
            if self.typed_config.metadata_filter_mode == MetadataFilterMode.AUTO:
                metadata_filters = (
                    await self._extract_auto_filter_groups_async(query, variable_pool)
                ) or []
            elif rendered_filters:
                metadata_filters = [rendered_filters]

        # 6. 构造检索请求（此时 query 必为非空：图片 data URI 或渲染后的文本）
        request = KnowledgeRetrievalRequest(
            query=final_query,
            source=KnowledgeRetrievalSource.WORKFLOW,
            kb_ids=kb_ids,
            knowledge_bases=self.typed_config.knowledge_bases,
            similarity_threshold=first_kb.similarity_threshold,
            vector_similarity_weight=vector_similarity_weight,
            top_k=self.typed_config.reranker_top_k or first_kb.top_k,
            retrieve_type=first_kb.retrieve_type,
            enable_graph_retrieval=enable_graph_retrieval,
            rerank_id=self.typed_config.reranker_id,
            rerank_mode=self.typed_config.rerank_mode,
            rerank_weights=self.typed_config.rerank_weights,
            metadata_filter_mode=self.typed_config.metadata_filter_mode,
            metadata_filters=metadata_filters,
        )
        if self.typed_config.metadata_filter_mode == MetadataFilterMode.AUTO:
            request.mark_metadata_filters_resolved()

        result = await retriever.retrieve(request, context)

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
