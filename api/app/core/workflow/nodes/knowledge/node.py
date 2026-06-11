import asyncio
import logging
import time
import uuid
from decimal import Decimal
from typing import Any

from langchain_core.documents import Document as LCDocument

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.models import RedBearRerank, RedBearModelConfig
from app.core.rag.llm.chat_model import Base
from app.core.rag.llm.embedding_model import OpenAIEmbed
from app.core.rag.metadata.builtin_resolver import BuiltinFieldResolver
from app.core.rag.metadata.filter_engine import (
    FilterCondition as EngineFilterCondition,
    FilterGroup as EngineFilterGroup,
    MetadataFilterEngine,
)
from app.core.rag.models.chunk import DocumentChunk
from app.core.rag.vdb.elasticsearch.elasticsearch_vector import ElasticSearchVectorFactory
from app.core.utils.datetime_utils import parse_iso_to_utc_naive
from app.core.workflow.engine.state_manager import WorkflowState
from app.core.workflow.engine.variable_pool import VariablePool
from app.core.workflow.nodes.base_node import BaseNode
from app.core.workflow.nodes.knowledge import KnowledgeRetrievalNodeConfig
from app.core.workflow.variable.base_variable import VariableType
from app.db import get_db_read
from app.models import knowledge_model, ModelType
from app.models.document_model import Document as DocumentModel
from app.repositories import knowledge_repository
from app.schemas.chunk_schema import RetrieveType
from app.schemas.knowledge_metadata_schema import (
    GroupLogic,
    MetadataFilterMode,
)
from app.services.knowledge_metadata_service import KnowledgeMetadataService
from app.services.metadata_auto_filter_service import MetadataAutoFilterService
from app.services.model_service import ModelConfigService

logger = logging.getLogger(__name__)


class KnowledgeRetrievalNode(BaseNode):
    def __init__(self, node_config: dict[str, Any], workflow_config: dict[str, Any], down_stream_nodes: list[str]):
        super().__init__(node_config, workflow_config, down_stream_nodes)
        self.typed_config: KnowledgeRetrievalNodeConfig | None = None

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
        if self.typed_config is None:
            self.typed_config = KnowledgeRetrievalNodeConfig(**self.config)
        return {
            "query": self._render_template(self.typed_config.query, variable_pool),
            "knowledge_bases": [kb_config.model_dump(mode="json") for kb_config in self.typed_config.knowledge_bases],
            "metadata_filter_mode": self.typed_config.metadata_filter_mode.value,
            "metadata_filters": self.typed_config.metadata_filters and {
                "logic": self.typed_config.metadata_filters.logic.value,
                "conditions": [{"field": c.field, "operator": c.operator, "value": c.value, "value_type": c.value_type} for c in self.typed_config.metadata_filters.conditions],
            },
            "metadata_model": self.typed_config.metadata_model and self.typed_config.metadata_model.model_dump(mode="json"),
        }

    @staticmethod
    def _build_kb_filter(kb_ids: list[uuid.UUID], permission: knowledge_model.PermissionType):
        """
        Build SQLAlchemy filter conditions for querying valid knowledge bases.

        Filters ensure:
        - Knowledge base ID is in the provided list
        - Permission type matches (Private / Share)
        - Knowledge base has indexed chunks
        - Knowledge base is in active status

        Args:
            kb_ids (list[UUID]): Candidate knowledge base IDs.
            permission (PermissionType): Required permission type.

        Returns:
            list: SQLAlchemy filter expressions.
        """
        return [
            knowledge_model.Knowledge.id.in_(kb_ids),
            knowledge_model.Knowledge.permission_id == permission,
            knowledge_model.Knowledge.chunk_num > 0,
            knowledge_model.Knowledge.status == 1
        ]

    @staticmethod
    def _deduplicate_docs(*doc_lists):
        """
        Deduplicate documents from multiple retrieval result lists
        while preserving original order.

        Deduplication is based on `doc.metadata["doc_id"]`.

        Args:
            *doc_lists: Multiple lists of retrieved documents.

        Returns:
            list: Deduplicated document list.
        """
        seen = set()
        unique = []
        for doc in (doc for lst in doc_lists for doc in lst):
            doc_id = doc.metadata["doc_id"]
            if doc_id not in seen:
                seen.add(doc_id)
                unique.append(doc)
        return unique

    def rerank(self, query: str, docs: list[DocumentChunk], top_k: int) -> list[DocumentChunk]:
        """
        Reorder the list of document blocks and return the top_k results most relevant to the query
        Args:
            query: query string
            docs: List of document chunk to be rearranged
            top_k: The number of top-level documents returned

        Returns:
            Rearranged document chunk list (sorted in descending order of relevance)

        Raises:
            ValueError: If the input document list is empty or top_k is invalid
        """
        reranker = self.get_reranker_model()
        # parameter validation
        if not docs:
            raise ValueError("retrieval chunks be empty")
        if top_k <= 0:
            raise ValueError("top_k must be a positive integer")
        try:
            # Convert to LangChain Document object
            documents = [
                LCDocument(
                    page_content=doc.page_content,  # Ensure that DocumentChunk possesses this attribute
                    metadata=doc.metadata or {}  # Deal with possible None metadata
                )
                for doc in docs
            ]

            # Perform reordering (compress_documents will automatically handle relevance scores and indexing)
            reranked_docs = list(reranker.compress_documents(documents, query))

            # Sort in descending order based on relevance score
            reranked_docs.sort(
                key=lambda x: x.metadata.get("relevance_score", 0),
                reverse=True
            )
            # Convert back to a list of DocumentChunk, and save the relevance_score to metadata["score"]
            result = []
            for item in reranked_docs[:top_k]:
                for doc in docs:
                    if doc.page_content == item.page_content:
                        doc.metadata["score"] = item.metadata["relevance_score"]
                        result.append(doc)
            return result
        except Exception as e:
            raise RuntimeError(f"Failed to rerank documents: {str(e)}") from e

    def get_reranker_model(self) -> RedBearRerank:
        """
        Retrieve and initialize a RedBear reranker model based on configuration.

        Raises:
            BusinessException: If configuration is missing or API keys are not set.
            RuntimeError: If the configured model is not of type RERANK.
        """
        with get_db_read() as db:
            config = ModelConfigService.get_model_by_id(db=db, model_id=self.typed_config.reranker_id)

            if not config:
                raise BusinessException("Configured model does not exist", BizCode.NOT_FOUND)

            if not config.api_keys or len(config.api_keys) == 0:
                raise BusinessException("Model configuration is missing API Key", BizCode.INVALID_PARAMETER)

            # 在 Session 关闭前提取所有需要的数据
            api_config = config.api_keys[0]
            model_name = api_config.model_name
            provider = api_config.provider
            api_key = api_config.api_key
            api_base = api_config.api_base
            model_type = config.type

        if model_type != ModelType.RERANK:
            raise RuntimeError("Model is not a reranker")

        reranker = RedBearRerank(
            RedBearModelConfig(
                model_name=model_name,
                provider=provider,
                api_key=api_key,
                base_url=api_base,
            )
        )
        return reranker

    async def knowledge_retrieval(self, db, query, db_knowledge, kb_config, document_ids_filter=None):
        rs = []
        if db_knowledge.type == knowledge_model.KnowledgeType.FOLDER:
            children = knowledge_repository.get_knowledges_by_parent_id(db=db, parent_id=db_knowledge.id)
            tasks = []
            for child in children:
                if not (child and child.chunk_num > 0 and child.status == 1):
                    continue
                child_kb_config = kb_config.model_copy()
                child_kb_config.kb_id = child.id
                tasks.append(self.knowledge_retrieval(db, query, child, child_kb_config, document_ids_filter))
            if tasks:
                result = await asyncio.gather(*tasks)
                for _ in result:
                    rs.extend(_)
            return rs
        vector_service = ElasticSearchVectorFactory().init_vector(knowledge=db_knowledge)
        indices = f"Vector_index_{kb_config.kb_id}_Node".lower()
        match kb_config.retrieve_type:
            case RetrieveType.PARTICIPLE:
                rs.extend(
                    await asyncio.to_thread(
                        vector_service.search_by_full_text, **{
                            "query": query,
                            "top_k": kb_config.top_k,
                            "indices": indices,
                            "score_threshold": kb_config.similarity_threshold,
                            "document_ids_filter": document_ids_filter
                        }
                    )
                )
            case RetrieveType.SEMANTIC:
                rs.extend(
                    await asyncio.to_thread(
                        vector_service.search_by_vector, **{
                            "query": query,
                            "top_k": kb_config.top_k,
                            "indices": indices,
                            "score_threshold": kb_config.vector_similarity_weight,
                            "document_ids_filter": document_ids_filter
                        }
                    )
                )
            case retrieve_type if retrieve_type in (RetrieveType.HYBRID, RetrieveType.Graph):
                rs1_task = asyncio.to_thread(
                    vector_service.search_by_vector, **{
                        "query": query,
                        "top_k": kb_config.top_k,
                        "indices": indices,
                        "score_threshold": kb_config.vector_similarity_weight,
                        "document_ids_filter": document_ids_filter
                    }
                )
                rs2_task = asyncio.to_thread(
                    vector_service.search_by_full_text, **{
                        "query": query,
                        "top_k": kb_config.top_k,
                        "indices": indices,
                        "score_threshold": kb_config.similarity_threshold,
                        "document_ids_filter": document_ids_filter
                    }
                )
                rs1, rs2 = await asyncio.gather(rs1_task, rs2_task)

                # Deduplicate hybrid retrieval results
                unique_rs = self._deduplicate_docs(rs1, rs2)
                if not unique_rs:
                    return []
                if self.typed_config.reranker_id:
                    rs.extend(
                        await asyncio.to_thread(
                            self.rerank,
                            **{"query": query, "docs": unique_rs, "top_k": kb_config.top_k}
                        )
                    )
                else:
                    rs.extend(sorted(
                        unique_rs,
                        key=lambda d: d.metadata.get("score", 0),
                        reverse=True
                    )[:kb_config.top_k])
                if kb_config.retrieve_type == RetrieveType.Graph:
                    from app.core.rag.common.settings import kg_retriever
                    # graphrag 路径暂不应用 document_ids 过滤（kg_retriever 接口待确认）
                    llm_key = self.model_balance(db_knowledge.llm)
                    emb_key = self.model_balance(db_knowledge.embedding)
                    chat_model = Base(
                        key=llm_key.api_key,
                        model_name=llm_key.model_name,
                        base_url=llm_key.api_base
                    )
                    embedding_model = OpenAIEmbed(
                        key=emb_key.api_key,
                        model_name=emb_key.model_name,
                        base_url=emb_key.api_base
                    )
                    doc = await asyncio.to_thread(
                        kg_retriever.retrieval,
                        question=query,
                        workspace_ids=[str(db_knowledge.workspace_id)],
                        kb_ids=[str(kb_config.kb_id)],
                        emb_mdl=embedding_model,
                        llm=chat_model
                    )
                    if doc:
                        rs.insert(0, DocumentChunk(
                            page_content=doc.get("page_content", ""),
                            metadata=doc.get("metadata", {})
                        ))
            case _:
                raise RuntimeError("Unknown retrieval type")
        return rs

    @staticmethod
    def _exclude_filtered(docs: list, document_ids_filter: list[str] | None) -> list:
        """Python 层双保险：从结果中排除命中过滤条件的文档（黑名单语义，与 ES must_not 一致）"""
        if not document_ids_filter:
            return docs
        exclude = set(document_ids_filter)
        return [
            doc for doc in docs
            if str((doc.metadata or {}).get("document_id")) not in exclude
        ]

    @staticmethod
    def _mf_debug(
        mode: str,
        status: str,
        start: float,
        *,
        reason: str | None = None,
        hit_count: int | None = None,
        condition_count: int = 0,
        skipped_fields: list | None = None,
        detail: str | None = None,
    ) -> dict:
        """构造 process_data.metadata_filter 诊断信息"""
        debug = {
            "mode": mode,
            "status": status,
            "reason": reason,
            "hit_count": hit_count,
            "condition_count": condition_count,
            "elapsed_ms": round((time.perf_counter() - start) * 1000, 2),
            "skipped_fields": skipped_fields,
        }
        if detail is not None:
            debug["detail"] = detail
        return debug

    async def _resolve_document_ids_filter(
        self,
        db,
        valid_kbs: list,
        query: str,
        variable_pool: VariablePool | None = None,
    ) -> tuple[list[str] | None, dict]:
        """
        节点级解析元数据过滤，得到给 ES 的 document_ids 黑名单（None 表示不过滤）。
        对节点内所有知识库复用同一份结果。

        Returns:
            (document_ids_filter, debug_info)
        """
        mode = self.typed_config.metadata_filter_mode
        start = time.perf_counter()
        logger.info(f"[MetaFilter] mode={mode}, kb_count={len(valid_kbs)}, "
                     f"metadata_filters={self.typed_config.metadata_filters}")

        if mode == MetadataFilterMode.DISABLED:
            return None, self._mf_debug("disabled", "skipped", start)

        try:
            # KB 存在性二次校验 + FOLDER 展开
            self._validate_metadata_filter_access(valid_kbs)
            filter_knowledge_ids = self._resolve_filter_knowledge_ids(db, valid_kbs)
            if not filter_knowledge_ids:
                return None, self._mf_debug(
                    str(mode),
                    "auto_fallback" if mode == MetadataFilterMode.AUTO else "manual_skipped",
                    start, reason="no_retrievable_knowledge",
                )

            if mode == MetadataFilterMode.MANUAL:
                return self._resolve_manual(db, filter_knowledge_ids, start, variable_pool)
            if mode == MetadataFilterMode.AUTO:
                return await self._resolve_auto(db, valid_kbs, filter_knowledge_ids, query, start)

            # 未知 mode 兜底
            return None, self._mf_debug(str(mode), "auto_fallback", start, reason="unknown_mode")
        except BusinessException:
            # manual 模式的配置错误（字段不存在 / 算子不兼容 / 内部错误）→ 让节点感知
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[MetaFilter] unexpected error, fallback to no-filter: {exc}")
            return None, self._mf_debug(
                str(mode), "auto_fallback", start, reason="unexpected_error", detail=str(exc)
            )

    @staticmethod
    def _validate_metadata_filter_access(valid_kbs: list) -> None:
        """KB 存在性二次校验（workspace 隔离 + chunk/status 已在 execute 入口完成）"""
        if not valid_kbs:
            raise BusinessException(
                "No valid knowledge base for metadata filtering",
                code=BizCode.KNOWLEDGE_NOT_FOUND,
            )
        for kb in valid_kbs:
            if kb is None or getattr(kb, "id", None) is None:
                raise BusinessException(
                    "Invalid knowledge base for metadata filtering",
                    code=BizCode.KNOWLEDGE_NOT_FOUND,
                )

    def _resolve_filter_knowledge_ids(self, db, valid_kbs: list) -> list[uuid.UUID]:
        """把节点内知识库（含 FOLDER 递归展开）汇总为可检索的子 KB ID 列表，visited 防环"""
        result: list[uuid.UUID] = []
        visited: set[uuid.UUID] = set()

        def expand(kb) -> None:
            if kb is None or kb.id in visited:
                return
            visited.add(kb.id)
            if kb.type == knowledge_model.KnowledgeType.FOLDER:
                children = knowledge_repository.get_knowledges_by_parent_id(db=db, parent_id=kb.id)
                for child in children:
                    if child and child.chunk_num > 0 and child.status == 1:
                        expand(child)
            else:
                result.append(kb.id)

        for kb in valid_kbs:
            expand(kb)
        return list(dict.fromkeys(result))

    @staticmethod
    def _resolve_metadata_defs(db, knowledge_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict[str, dict]]:
        """拉取每个 KB 的元数据字段定义"""
        return {
            knowledge_id: KnowledgeMetadataService.get_metadata_defs_for_filtering(db, knowledge_id)
            for knowledge_id in knowledge_ids
        }

    @staticmethod
    def _get_common_metadata_fields(metadata_defs_by_kb: dict[Any, dict[str, dict]]) -> set[str]:
        """取所有 KB 共有且类型一致的字段名"""
        field_names: set[str] = set()
        for metadata_defs in metadata_defs_by_kb.values():
            field_names.update(metadata_defs.keys())

        common_fields: set[str] = set()
        for field_name in field_names:
            field_types = set()
            all_have_field = True
            for metadata_defs in metadata_defs_by_kb.values():
                if field_name not in metadata_defs:
                    all_have_field = False
                    break
                field_types.add(metadata_defs[field_name]["type"])
            if all_have_field and len(field_types) == 1:
                common_fields.add(field_name)
        return common_fields

    def _resolve_common_metadata_defs(
        self, metadata_defs_by_kb: dict[Any, dict[str, dict]]
    ) -> dict[str, dict]:
        """多 KB 公共字段收敛后的字段定义子集 {field_name: {type, is_builtin}}"""
        common = self._get_common_metadata_fields(metadata_defs_by_kb)
        result: dict[str, dict] = {}
        for defs in metadata_defs_by_kb.values():
            for name in common:
                if name in defs and name not in result:
                    result[name] = defs[name]
        return result

    @staticmethod
    def _build_filter_group(metadata_filters, common_fields: set[str], variable_pool: VariablePool | None = None) -> EngineFilterGroup | None:
        """把节点级单组 metadata_filters 中的条件按公共字段名过滤后构造引擎侧 FilterGroup。
        如果 variable_pool 可用，渲染 value 中的 {{变量}} 模板。"""
        conditions = []
        for condition in metadata_filters.conditions:
            if condition.field not in common_fields:
                continue
            value = condition.value
            # Render variable templates in value (e.g. {{sys.message}})
            if variable_pool and isinstance(value, str) and "{{" in value:
                value = BaseNode._render_template(value, variable_pool, strict=False)
            conditions.append(EngineFilterCondition(field=condition.field, operator=condition.operator, value=value))
        if not conditions:
            return None
        return EngineFilterGroup(conditions=conditions, logic=metadata_filters.logic.value)

    @staticmethod
    def _execute_metadata_filter_for_kbs(
        db,
        knowledge_ids: list[uuid.UUID],
        filter_groups: list[EngineFilterGroup],
        metadata_defs_by_kb: dict[uuid.UUID, dict[str, dict]],
    ) -> list[uuid.UUID]:
        """遍历每个 KB 执行过滤引擎，取 document_id 并集"""
        document_ids: set[uuid.UUID] = set()
        engine = MetadataFilterEngine(db)
        for knowledge_id in knowledge_ids:
            matched_ids = engine.execute(
                knowledge_id=knowledge_id,
                filter_groups=filter_groups,
                metadata_defs=metadata_defs_by_kb[knowledge_id],
            )
            document_ids.update(matched_ids)
        return list(document_ids)

    def _resolve_manual(self, db, filter_knowledge_ids: list[uuid.UUID], start: float, variable_pool: VariablePool | None = None) -> tuple[list[str] | None, dict]:
        metadata_filters = self.typed_config.metadata_filters
        if not metadata_filters or not metadata_filters.conditions:
            logger.warning("[MetaFilter] manual mode but metadata_filters is empty")
            return None, self._mf_debug("manual", "manual_empty", start, reason="metadata_filters is empty")

        metadata_defs_by_kb = self._resolve_metadata_defs(db, filter_knowledge_ids)
        common_fields = self._get_common_metadata_fields(metadata_defs_by_kb)
        logger.info(f"[MetaFilter] metadata_defs_by_kb keys: {list(metadata_defs_by_kb.keys())}, "
                     f"common_fields: {common_fields}, "
                     f"metadata_filters fields: {[c.field for c in metadata_filters.conditions]}")
        filter_group = self._build_filter_group(metadata_filters, common_fields, variable_pool)
        if not filter_group:
            logger.warning("[MetaFilter] manual mode but no common metadata fields matched")
            return None, self._mf_debug("manual", "manual_skipped", start, reason="no_common_metadata_fields")

        logger.info(f"[MetaFilter] filter_group: logic={filter_group.logic}, "
                     f"conditions: {[{'field': c.field, 'op': c.operator, 'val': c.value} for c in filter_group.conditions]}")

        try:
            document_ids = self._execute_metadata_filter_for_kbs(
                db=db,
                knowledge_ids=filter_knowledge_ids,
                filter_groups=[filter_group],
                metadata_defs_by_kb=metadata_defs_by_kb,
            )
        except BusinessException:
            # 字段不存在 / 算子不兼容 → 配置错误，原样向上抛
            raise
        except Exception as exc:  # noqa: BLE001
            raise BusinessException(
                f"metadata filter execution failed: {exc}",
                code=BizCode.INTERNAL_ERROR,
            ) from exc

        doc_ids = [str(document_id) for document_id in document_ids]
        condition_count = len(filter_group.conditions)
        status = "applied" if doc_ids else "applied_empty"
        logger.info(f"[MetaFilter] manual result: status={status}, hit_count={len(doc_ids)}, "
                     f"doc_ids={doc_ids[:5]}{'...' if len(doc_ids) > 5 else ''}")
        return doc_ids, self._mf_debug(
            "manual", status, start, hit_count=len(doc_ids), condition_count=condition_count
        )

    async def _resolve_auto(
        self,
        db,
        valid_kbs: list,
        filter_knowledge_ids: list[uuid.UUID],
        query: str,
        start: float,
    ) -> tuple[list[str] | None, dict]:
        """Auto mode: use LLM to extract metadata filter conditions from user query"""
        # 1. Load LLM model (metadata_model → KB's llm_id)
        llm, gen_conf = self._load_metadata_llm(db, valid_kbs)
        if llm is None:
            logger.warning("[MetaFilter] auto mode: no LLM available (metadata_model not set, KB has no llm_id)")
            return None, self._mf_debug("auto", "auto_fallback", start, reason="model_invalid")

        # 2. Get metadata defs (reuse existing methods)
        metadata_defs_by_kb = self._resolve_metadata_defs(db, filter_knowledge_ids)
        common_defs = self._resolve_common_metadata_defs(metadata_defs_by_kb)
        if not common_defs:
            logger.warning("[MetaFilter] auto mode: no common metadata fields across KBs")
            return None, self._mf_debug("auto", "auto_fallback", start, reason="no_common_metadata_fields")

        # 3. Build metadata value map for LLM prompt (used for debugging/future prompt enhancement)
        gen_meta_input = self._build_gen_meta_input(db, filter_knowledge_ids, common_defs)
        logger.info(f"[MetaFilter] auto: gen_meta_input fields: {list(gen_meta_input.keys())}, "
                     f"total values per field: {', '.join(f'{k}: {len(v)} unique' for k, v in gen_meta_input.items())}")

        # 4. Call MetadataAutoFilterService to generate filter groups
        try:
            filter_groups = MetadataAutoFilterService.generate_filter_groups(
                query=query,
                metadata_defs=common_defs,
                llm=llm,
                gen_conf=gen_conf,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[MetaFilter] auto LLM call failed: {exc}")
            return None, self._mf_debug("auto", "auto_fallback", start, reason="llm_error", detail=str(exc))

        if not filter_groups:
            logger.info("[MetaFilter] auto mode: LLM extracted no conditions")
            return None, self._mf_debug("auto", "auto_applied_empty", start, condition_count=0)

        # 5. Normalize conditions — auto always uses AND logic (single group)
        normalized_conditions = []
        for group in filter_groups:
            for cond in group.conditions:
                normalized = self._normalize_meta_filter(cond, common_defs)
                if normalized:
                    normalized_conditions.append(normalized)

        if not normalized_conditions:
            logger.info("[MetaFilter] auto mode: all conditions invalid after normalization")
            return None, self._mf_debug("auto", "auto_applied_empty", start, condition_count=0, reason="all_conditions_invalid")

        filter_group = EngineFilterGroup(conditions=normalized_conditions, logic="and")

        # 6. Execute the filter engine
        try:
            document_ids = self._execute_metadata_filter_for_kbs(
                db=db,
                knowledge_ids=filter_knowledge_ids,
                filter_groups=[filter_group],
                metadata_defs_by_kb=metadata_defs_by_kb,
            )
        except BusinessException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise BusinessException(
                f"auto metadata filter execution failed: {exc}",
                code=BizCode.INTERNAL_ERROR,
            ) from exc

        doc_ids = [str(document_id) for document_id in document_ids]
        condition_count = len(filter_group.conditions)
        status = "auto_applied" if doc_ids else "auto_applied_empty"
        logger.info(f"[MetaFilter] auto result: status={status}, hit_count={len(doc_ids)}, "
                     f"condition_count={condition_count}")
        return doc_ids, self._mf_debug(
            "auto", status, start,
            hit_count=len(doc_ids), condition_count=condition_count,
        )

    def _load_metadata_llm(self, db, valid_kbs: list) -> tuple[Base | None, dict]:
        """Load chat model for auto metadata filtering.

        Priority: metadata_model (node config) → KB's llm_id → None (no LLM available)

        Returns:
            (llm_instance, gen_conf_dict) — gen_conf is passed to llm.chat()
        """
        # Try metadata_model from node config first
        metadata_model = self.typed_config.metadata_model
        if metadata_model and metadata_model.model_id:
            try:
                config = ModelConfigService.get_model_by_id(db=db, model_id=metadata_model.model_id)
                if config and config.api_keys:
                    api_key = self.model_balance(config)
                    llm = Base(
                        key=api_key.api_key,
                        model_name=metadata_model.model or api_key.model_name,
                        base_url=api_key.api_base,
                    )
                    gen_conf = self._build_gen_conf(metadata_model)
                    return llm, gen_conf
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[MetaFilter] metadata_model load failed: {exc}")

        # Fallback: try KB's llm_id (pick the first KB that has one)
        for kb in valid_kbs:
            kb_llm_id = getattr(kb, "llm_id", None)
            if kb_llm_id:
                try:
                    llm_config = ModelConfigService.get_model_by_id(db=db, model_id=kb_llm_id)
                    if llm_config and llm_config.api_keys:
                        api_key = self.model_balance(llm_config)
                        llm = Base(
                            key=api_key.api_key,
                            model_name=api_key.model_name,
                            base_url=api_key.api_base,
                        )
                        # Use default gen_conf (temperature=0 for structured extraction)
                        return llm, {"temperature": 0}
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"[MetaFilter] KB llm_id load failed: {exc}")
                    continue

        return None, {}

    @staticmethod
    def _build_gen_conf(metadata_model) -> dict:
        """Build gen_conf dict from metadata_model.completion_params for llm.chat()"""
        params = metadata_model.completion_params
        gen_conf: dict[str, Any] = {}

        if params.temperature is not None:
            gen_conf["temperature"] = params.temperature
        if params.max_tokens is not None:
            gen_conf["max_tokens"] = params.max_tokens

        # Enable/value toggle params — only add if enable is True and value is not None
        if params.top_p.enable and params.top_p.value is not None:
            gen_conf["top_p"] = params.top_p.value
        if params.top_k.enable and params.top_k.value is not None:
            gen_conf["top_k"] = params.top_k.value
        if params.seed.enable and params.seed.value is not None:
            gen_conf["seed"] = params.seed.value
        if params.repetition_penalty.enable and params.repetition_penalty.value is not None:
            gen_conf["repetition_penalty"] = params.repetition_penalty.value
        if params.frequency_penalty.enable and params.frequency_penalty.value is not None:
            gen_conf["frequency_penalty"] = params.frequency_penalty.value
        if params.presence_penalty.enable and params.presence_penalty.value is not None:
            gen_conf["presence_penalty"] = params.presence_penalty.value
        if params.stop.enable and params.stop.value is not None:
            gen_conf["stop"] = params.stop.value

        if params.json_output:
            gen_conf["response_format"] = {"type": "json_object"}

        return gen_conf

    @staticmethod
    def _build_gen_meta_input(
        db,
        knowledge_ids: list[uuid.UUID],
        common_defs: dict[str, dict],
    ) -> dict[str, dict[str, list[str]]]:
        """Build {field_name: {value: [doc_id_list]}} from DB for LLM prompt.

        This gives the LLM a snapshot of available metadata values to match against.
        Handles both custom fields (JSONB) and builtin fields (real columns).
        """
        result: dict[str, dict[str, list[str]]] = {}

        for field_name, field_def in common_defs.items():
            value_map: dict[str, list[str]] = {}
            is_builtin = field_def.get("is_builtin", False)

            for knowledge_id in knowledge_ids:
                if is_builtin:
                    # Builtin fields: query from real Document columns
                    builtin_field = BuiltinFieldResolver.resolve(field_name)
                    if builtin_field:
                        column = getattr(DocumentModel, builtin_field.mapping, None)
                        if column is not None:
                            rows = db.query(column, DocumentModel.id).filter(
                                DocumentModel.kb_id == knowledge_id,
                                DocumentModel.status == 1,
                            ).all()
                            for row_val, doc_id in rows:
                                str_val = KnowledgeRetrievalNode._stringify_meta_value(row_val)
                                if str_val and str_val not in value_map:
                                    value_map[str_val] = []
                                if str_val:
                                    value_map[str_val].append(str(doc_id))
                else:
                    # Custom fields: extract from JSONB meta_data column
                    rows = db.query(DocumentModel.meta_data, DocumentModel.id).filter(
                        DocumentModel.kb_id == knowledge_id,
                        DocumentModel.status == 1,
                    ).all()
                    for meta_data, doc_id in rows:
                        if isinstance(meta_data, dict) and field_name in meta_data:
                            str_val = KnowledgeRetrievalNode._stringify_meta_value(meta_data[field_name])
                            if str_val and str_val not in value_map:
                                value_map[str_val] = []
                            if str_val:
                                value_map[str_val].append(str(doc_id))

            if value_map:
                result[field_name] = value_map

        return result

    @staticmethod
    def _stringify_meta_value(value: Any) -> str | None:
        """Convert DB metadata value to string for LLM prompt input."""
        if value is None:
            return None
        if isinstance(value, bool):
            return str(value).lower()
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, (int, float)):
            # Format numbers: keep ints as ints, floats with reasonable precision
            if isinstance(value, float) and value.is_integer():
                return str(int(value))
            return str(value)
        return str(value)

    @staticmethod
    def _normalize_meta_filter(
        condition: EngineFilterCondition,
        metadata_defs: dict[str, dict],
    ) -> EngineFilterCondition | None:
        """Validate and normalize an LLM-generated filter condition.

        Checks field existence, operator compatibility with field type,
        and value type consistency. Returns None if the condition is invalid.
        """
        field_name = condition.field
        field_def = metadata_defs.get(field_name)
        if not field_def:
            logger.warning(f"[MetaFilter] auto: unknown field '{field_name}', skipping")
            return None

        operator = condition.operator
        field_type = field_def.get("type")

        # Operator alias mapping (similar to MetadataAutoFilterService._OPERATOR_ALIASES)
        OPERATOR_ALIASES = {
            "contains": "contains",
            "not_contains": "not_contains",
            "starts_with": "starts_with",
            "ends_with": "ends_with",
            "eq": "eq",
            "ne": "ne",
            "gt": "gt",
            "lt": "lt",
            "gte": "gte",
            "lte": "lte",
            "before": "before",
            "after": "after",
            "is_empty": "is_empty",
            "not_empty": "not_empty",
            "in": "in",
            "not_in": "not_in",
        }
        SUPPORTED_OPERATORS = {
            "string": {
                "eq", "ne", "contains", "not_contains",
                "starts_with", "ends_with", "is_empty", "not_empty",
                "in", "not_in",
            },
            "number": {"eq", "ne", "gt", "lt", "gte", "lte", "is_empty", "not_empty"},
            "time": {"eq", "before", "after", "is_empty", "not_empty"},
        }

        # Normalize operator
        normalized_op = OPERATOR_ALIASES.get(operator, operator)
        if normalized_op not in SUPPORTED_OPERATORS.get(field_type, set()):
            logger.warning(f"[MetaFilter] auto: operator '{operator}' not supported for field type '{field_type}', skipping")
            return None

        # Validate / normalize value
        value = condition.value
        if normalized_op in ("is_empty", "not_empty"):
            value = None
        elif field_type == "time" and value is not None:
            # Validate time values
            try:
                dt = parse_iso_to_utc_naive(str(value))
                if dt is None:
                    logger.warning(f"[MetaFilter] auto: invalid time value '{value}', skipping")
                    return None
            except ValueError:
                logger.warning(f"[MetaFilter] auto: invalid time format '{value}', skipping")
                return None
        elif field_type == "number" and value is not None and normalized_op not in ("is_empty", "not_empty"):
            try:
                num = float(value)
                value = int(num) if num.is_integer() else num
            except (TypeError, ValueError):
                logger.warning(f"[MetaFilter] auto: invalid number value '{value}', skipping")
                return None

        return EngineFilterCondition(
            field=field_name,
            operator=normalized_op,
            value=value,
        )

    async def execute(self, state: WorkflowState, variable_pool: VariablePool) -> Any:
        """
        Execute the knowledge retrieval workflow node.

        Steps:
        1. Render query template using workflow state
        2. Resolve accessible knowledge bases
        3. Initialize Elasticsearch vector service
        4. Perform retrieval based on configured retrieve type
        5. Deduplicate results if necessary
        6. Serialize and return retrieved chunks

        Args:
            state (WorkflowState): Current workflow execution state.
            variable_pool: Variable Pool

        Returns:
            Any: List of retrieved knowledge chunks (dict format).

        Raises:
            RuntimeError: If no valid knowledge base is found or access is denied.
        """
        self.typed_config = KnowledgeRetrievalNodeConfig(**self.config)
        if not self.typed_config.knowledge_bases:
            return []
        query = self._render_template(self.typed_config.query, variable_pool)
        with get_db_read() as db:
            knowledge_bases = self.typed_config.knowledge_bases

            # 加载并校验所有知识库（沿用现有 workspace 隔离 + chunk_num/status 校验）
            valid_kbs: list[tuple[Any, Any]] = []
            for kb_config in knowledge_bases:
                db_knowledge = knowledge_repository.get_knowledge_by_id(db=db, knowledge_id=kb_config.kb_id)
                if not (db_knowledge and db_knowledge.chunk_num > 0 and db_knowledge.status == 1):
                    logger.warning("The knowledge base does not exist or access is denied.")
                    continue
                valid_kbs.append((kb_config, db_knowledge))

            if not valid_kbs:
                return []

            # === 节点级元数据过滤：仅解析一次，对所有 KB 复用同一份 document_ids_filter ===
            document_ids_filter, metadata_filter_debug = await self._resolve_document_ids_filter(
                db=db,
                valid_kbs=[db_knowledge for _, db_knowledge in valid_kbs],
                query=query,
                variable_pool=variable_pool,
            )

            rs = []
            tasks = [
                self.knowledge_retrieval(db, query, db_knowledge, kb_config, document_ids_filter)
                for kb_config, db_knowledge in valid_kbs
            ]
            if tasks:
                result = await asyncio.gather(*tasks)
                for _ in result:
                    rs.extend(_)

            
            if not rs:
                return {
                    "chunks": [],
                    "citations": [],
                    "_metadata_filter_result": metadata_filter_debug,
                }
            if self.typed_config.reranker_id:
                final_rs = await asyncio.to_thread(
                    self.rerank,
                    **{"query": query, "docs": rs, "top_k": self.typed_config.reranker_top_k}
                )
            else:
                final_rs = sorted(
                    rs,
                    key=lambda d: d.metadata.get("score", 0),
                    reverse=True
                )[:self.typed_config.reranker_top_k]

            logger.info(
                f"Node {self.node_id}: knowledge base retrieval completed, results count: {len(final_rs)}"
            )
            citations = []
            seen_doc_ids = set()
            for chunk in final_rs:
                meta = chunk.metadata or {}
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
            return {
                "chunks": [chunk.page_content for chunk in final_rs],
                "citations": citations,
                "_metadata_filter_result": metadata_filter_debug,
            }
