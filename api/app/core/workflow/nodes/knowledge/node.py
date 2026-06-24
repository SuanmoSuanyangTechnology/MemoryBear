import asyncio
import logging
import re
from typing import Any

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.rag.llm.chat_model import Base as ChatModelBase
from app.core.workflow.engine.state_manager import WorkflowState
from app.core.workflow.engine.variable_pool import VariablePool
from app.core.workflow.nodes.base_node import BaseNode
from app.core.workflow.nodes.knowledge import KnowledgeRetrievalNodeConfig
from app.core.workflow.variable.base_variable import VariableType
from app.db import get_db_read
from app.schemas.knowledge_metadata_schema import FilterCondition, FilterGroup, MetadataFilterMode
from app.schemas.knowledge_retrieval_schema import KnowledgeRetrievalRequest
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

    def _build_auto_filter_llm(self, db) -> ChatModelBase:
        """auto 模式：用节点配置的模型 + 参数构造 LLM

        复用 service._build_chat_model 的构造方式（chat_model.Base），与
        MetadataAutoFilterService.generate_filter_groups 期望的 llm.chat() 接口一致。
        """
        model_cfg = self.typed_config.metadata_model
        config = ModelConfigService.get_model_by_id(db=db, model_id=model_cfg.model_id)
        if not config:
            raise BusinessException(
                "auto 模式配置的模型不存在", code=BizCode.NOT_FOUND
            )
        if not config.api_keys:
            raise BusinessException(
                "auto 模式配置的模型缺少 API Key", code=BizCode.INVALID_PARAMETER
            )
        api_key = self.model_balance(config)  # BaseNode.model_balance -> ModelApiKey
        return ChatModelBase(
            key=api_key.api_key,
            model_name=api_key.model_name,
            base_url=api_key.api_base,
        )

    def _build_gen_conf(self) -> dict:
        """AgentModelConfig.completion_params -> chat_model.Base 的 gen_conf

        auto 模式底层 LLM 是知识库工程师 generate_filter_groups 定好的 chat_model.Base，
        其 _chat 将 gen_conf 直接透传给 OpenAI chat.completions.create(**gen_conf)，
        并经 _clean_conf 过滤掉不支持的键。

        因此配置层与 agent 节点完全一致（不阉割），运行时按 OpenAI API 参数语义全量映射；
        少数 chat_model.Base 不支持的键（top_k / repetition_penalty 等）会被 _clean_conf 自动丢弃，
        并在此给出 warning 告知用户该参数在 auto 过滤场景下不生效。
        """
        import json as _json

        p = self.typed_config.metadata_model.completion_params
        conf: dict[str, Any] = {}
        # 基础生成参数（OpenAI API 原生支持）
        if p.temperature is not None:
            conf["temperature"] = p.temperature
        if p.max_tokens is not None:
            # chat_model.Base._clean_conf 会删掉 max_tokens，改用 OpenAI 的 max_completion_tokens
            conf["max_completion_tokens"] = p.max_tokens
        if p.top_p.enable and p.top_p.value is not None:
            conf["top_p"] = p.top_p.value
        if p.seed.enable and p.seed.value is not None:
            conf["seed"] = p.seed.value
        if p.frequency_penalty.enable and p.frequency_penalty.value is not None:
            conf["frequency_penalty"] = p.frequency_penalty.value
        if p.presence_penalty.enable and p.presence_penalty.value is not None:
            conf["presence_penalty"] = p.presence_penalty.value
        if p.stop.enable and p.stop.value:
            conf["stop"] = p.stop.value[:4]
        if p.response_format.enable and p.response_format.value:
            conf["response_format"] = {"type": p.response_format.value}
        if p.extra_headers.enable and p.extra_headers.value:
            try:
                conf["extra_headers"] = _json.loads(p.extra_headers.value)
            except (ValueError, TypeError) as e:
                logger.warning(
                    f"node: {self.node_id} auto filter: extra_headers JSON parse failed: {e}"
                )

        # 下列参数 chat_model.Base 不支持（_clean_conf 会丢弃），配了也不生效，给出 warning 提示
        unsupported_hits = []
        if p.top_k.enable and p.top_k.value is not None:
            unsupported_hits.append("top_k")
            conf["top_k"] = p.top_k.value
        if p.repetition_penalty.enable and p.repetition_penalty.value is not None:
            unsupported_hits.append("repetition_penalty")
            conf["repetition_penalty"] = p.repetition_penalty.value
        if getattr(p, "search", False):
            unsupported_hits.append("search")
        if getattr(p.thinking, "enable", False):
            unsupported_hits.append("thinking")
        if unsupported_hits:
            logger.warning(
                f"node: {self.node_id} auto filter: parameters {unsupported_hits} are not supported "
                f"by the underlying chat_model.Base and will be ignored"
            )

        return conf

    def _extract_auto_filter_groups(
        self,
        query: str,
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

        with get_db_read() as db:
            # 1. 取各知识库的元数据定义，求公共字段（与 service._build_metadata_document_filter 一致）
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
                return []

            # 2. 构造配置好的 LLM（含模型参数）
            llm = self._build_auto_filter_llm(db)

        # 3. 调用知识库工程师已写好的提取方法，传入自定义 gen_conf（应用模型参数）
        gen_conf = self._build_gen_conf()
        logger.info(
            "node: %s auto filter: query=%r, fields=%s, gen_conf=%s",
            self.node_id, query, list(common_metadata_defs.keys()), gen_conf,
        )
        filter_groups = MetadataAutoFilterService.generate_filter_groups(
            query=query,
            metadata_defs=common_metadata_defs,
            llm=llm,
            gen_conf=gen_conf,
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

        # 2.5 auto 模式：节点层用配置好的模型 + 参数，提取出源数据过滤条件（list[FilterGroup]，配置层类型）
        auto_filter_groups: list | None = None
        if self.typed_config.metadata_filter_mode == MetadataFilterMode.AUTO:
            # generate_filter_groups 内部走同步 LLM.chat 网络调用，放到工作线程避免阻塞事件循环
            auto_filter_groups = await asyncio.to_thread(
                self._extract_auto_filter_groups, query
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
            metadata_filters=auto_filter_groups or ([rendered_filters] if rendered_filters else []),
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