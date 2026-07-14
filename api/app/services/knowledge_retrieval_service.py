import asyncio
import logging
import time
import uuid
from enum import Enum
from typing import Any, Sequence

from app.core.config import settings
from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.rag.metadata.filter_engine import (
    FilterCondition as EngineFilterCondition,
    FilterGroup as EngineFilterGroup,
)
from app.core.rag.models.chunk import DocumentChunk
from app.core.rag.retrieval.async_elasticsearch import (
    AsyncElasticSearchRetrieval,
    AsyncElasticsearchClientProvider,
)
from app.core.rag.retrieval.async_models import AsyncRetrievalModelGateway
from app.core.rag.retrieval.exceptions import KnowledgeRetrievalConfigError
from app.core.rag.retrieval.graph_bridge import GraphRetrievalBridge
from app.core.rag.retrieval.models import (
    ModelRuntimeSnapshot,
    RetrievalParams,
    RetrievalPreparation,
    RetrievalPrincipal,
    RetrievalSearchOptions,
    RetrievalTarget,
)
from app.schemas.chunk_schema import RetrieveType
from app.schemas.knowledge_metadata_schema import MetadataFilterMode
from app.schemas.knowledge_retrieval_schema import (
    KnowledgeRetrievalRequest,
    KnowledgeRetrievalResult,
)
from app.services.knowledge_retrieval_preparation import KnowledgeRetrievalPreparation

logger = logging.getLogger(__name__)

ModelApiKeySnapshot = ModelRuntimeSnapshot


class KnowledgeRetrievalAccessDenied(Exception):
    pass


class KnowledgeRetrievalService:
    """Native asynchronous knowledge-base retrieval facade."""

    @staticmethod
    def _new_retrieval_log_id() -> str:
        return uuid.uuid4().hex[:8]

    @staticmethod
    def _elapsed_ms(started_at: float) -> int:
        return max(0, int((time.perf_counter() - started_at) * 1000))

    @staticmethod
    def _compact_id(value: Any) -> str:
        text = str(value)
        return text[:8] if len(text) > 8 else text

    @classmethod
    def _compact_ids(cls, values: Sequence[Any], limit: int = 10) -> list[str]:
        compacted = [cls._compact_id(value) for value in values[:limit]]
        if len(values) > limit:
            compacted.append(f"+{len(values) - limit}")
        return compacted

    @classmethod
    def _format_log_fields(cls, fields: dict[str, Any]) -> str:
        return " ".join(
            f"{key}={cls._format_log_value(value)}"
            for key, value in fields.items()
        )

    @classmethod
    def _format_log_value(cls, value: Any) -> str:
        if value is None:
            return "none"
        if isinstance(value, bool):
            return str(value).lower()
        if isinstance(value, Enum):
            return str(value.value)
        if isinstance(value, (list, tuple, set)):
            return "[" + ",".join(cls._format_log_value(item) for item in value) + "]"
        return str(value)

    @classmethod
    def _build_retrieval_start_log_fields(
        cls,
        log_id: str,
        request: KnowledgeRetrievalRequest,
        principal: RetrievalPrincipal | None,
    ) -> dict[str, Any]:
        return {
            "id": log_id,
            "user": (
                principal.username
                if principal and principal.username
                else principal.id if principal else "anonymous"
            ),
            "kb_count": len(request.kb_ids or []),
            "ex_id_count": len(request.ex_ids or []),
            "knowledge_base_count": len(request.knowledge_bases or []),
            "query_len": len(request.query or ""),
            "type": request.retrieve_type,
            "top_k": request.top_k,
            "top_n": request.top_n,
            "metadata_mode": request.metadata_filter_mode,
            "async_mode": "native",
        }

    @classmethod
    async def retrieve_async(
        cls,
        request: KnowledgeRetrievalRequest,
        principal: RetrievalPrincipal | None = None,
    ) -> KnowledgeRetrievalResult:
        """Retrieve with short database snapshots and native asynchronous I/O."""

        principal = RetrievalPrincipal.from_user(principal)
        log_id = cls._new_retrieval_log_id()
        started_at = time.perf_counter()
        logger.info(
            "[Retrieval] start %s",
            cls._format_log_fields(
                cls._build_retrieval_start_log_fields(log_id, request, principal)
            ),
        )

        snapshot_started_at = time.perf_counter()
        preparation = await KnowledgeRetrievalPreparation.prepare(request, principal)
        snapshot_ms = cls._elapsed_ms(snapshot_started_at)
        if not preparation.targets:
            return cls._finish_empty(log_id, started_at, "no_targets", snapshot_ms)

        models = AsyncRetrievalModelGateway()
        metadata_started_at = time.perf_counter()
        filter_groups = await cls._build_metadata_filter_groups(
            request,
            preparation,
            models,
        )
        metadata_llm_ms = cls._elapsed_ms(metadata_started_at)

        metadata_query_started_at = time.perf_counter()
        document_ids_include = await KnowledgeRetrievalPreparation.resolve_metadata_document_ids(
            preparation,
            filter_groups,
        )
        metadata_query_ms = cls._elapsed_ms(metadata_query_started_at)
        cls._log_metadata_filter(
            log_id,
            request,
            preparation,
            filter_groups,
            document_ids_include,
        )
        if document_ids_include == []:
            return cls._finish_empty(
                log_id,
                started_at,
                "metadata_filter_empty",
                snapshot_ms,
                metadata_llm_ms,
                metadata_query_ms,
            )

        client = await AsyncElasticsearchClientProvider.get_shared_client()
        store = AsyncElasticSearchRetrieval(client, models)
        chunks = await cls._retrieve_targets(
            request,
            preparation,
            document_ids_include,
            store,
            models,
            log_id,
        )
        if preparation.graph:
            graph_document = await GraphRetrievalBridge.retrieve(preparation.graph)
            if graph_document:
                chunks.insert(0, graph_document)

        chunks = cls._include_document_ids(chunks, document_ids_include)
        logger.info(
            "[Retrieval] finish %s",
            cls._format_log_fields(
                {
                    "id": log_id,
                    "reason": "ok",
                    "target_count": len(preparation.targets),
                    "document_filter_count": len(document_ids_include or []),
                    "final_count": len(chunks),
                    "db_snapshot_ms": snapshot_ms,
                    "metadata_llm_ms": metadata_llm_ms,
                    "metadata_query_ms": metadata_query_ms,
                    "elapsed_ms": cls._elapsed_ms(started_at),
                    "async_mode": "native",
                }
            ),
        )
        return KnowledgeRetrievalResult(chunks=chunks)

    @classmethod
    def retrieve(cls, *args: Any, **kwargs: Any) -> KnowledgeRetrievalResult:
        """Compatibility marker for callers that still need migration."""

        del cls, args, kwargs
        raise RuntimeError("Synchronous knowledge retrieval is no longer supported")

    @classmethod
    async def _build_metadata_filter_groups(
        cls,
        request: KnowledgeRetrievalRequest,
        preparation: RetrievalPreparation,
        models: AsyncRetrievalModelGateway,
    ) -> list[EngineFilterGroup]:
        if request.metadata_filter_mode == MetadataFilterMode.DISABLED:
            return []
        if request.metadata_filter_mode == MetadataFilterMode.MANUAL:
            if not request.metadata_filters:
                return []
            return cls._build_common_filter_groups(
                request.metadata_filters,
                set(preparation.common_metadata_defs.keys()),
            )
        if request.metadata_filter_mode == MetadataFilterMode.AUTO:
            if request.metadata_filters or request.metadata_filters_prepared:
                return cls._build_common_filter_groups(
                    request.metadata_filters,
                    set(preparation.common_metadata_defs.keys()),
                )
            if not preparation.common_metadata_defs or not preparation.metadata_llm:
                return []
            return await models.generate_metadata_filters(
                request.query,
                preparation.common_metadata_defs,
                preparation.metadata_llm,
            )
        raise BusinessException(
            f"metadata_filter_mode 不支持: {request.metadata_filter_mode}",
            code=BizCode.INVALID_PARAMETER,
        )

    @classmethod
    async def _retrieve_targets(
        cls,
        request: KnowledgeRetrievalRequest,
        preparation: RetrievalPreparation,
        document_ids_include: list[str] | None,
        store: AsyncElasticSearchRetrieval,
        models: AsyncRetrievalModelGateway,
        log_id: str,
    ) -> list[DocumentChunk]:
        targets = preparation.targets
        if not targets:
            return []

        max_workers = max(
            1,
            min(len(targets), settings.KNOWLEDGE_RETRIEVAL_MAX_WORKERS or 3),
        )
        logger.info(
            "[Retrieval] targets %s",
            cls._format_log_fields(
                {
                    "id": log_id,
                    "target_count": len(targets),
                    "max_workers": max_workers,
                    "target_kbs": cls._compact_ids([target.knowledge_id for target in targets]),
                    "async_mode": "native",
                }
            ),
        )
        semaphore = asyncio.Semaphore(max_workers)

        async def retrieve_one(
            index: int,
            target: RetrievalTarget,
        ) -> tuple[int, list[DocumentChunk]]:
            async with semaphore:
                chunks = await cls._retrieve_single_target(
                    request,
                    target,
                    document_ids_include,
                    store,
                    models,
                    use_request_reranker=(
                        request.rerank_id is not None
                        and len(targets) == 1
                        and target.params.retrieve_type == RetrieveType.HYBRID
                    ),
                    request_reranker=preparation.request_reranker,
                )
                return index, chunks

        tasks = [
            asyncio.create_task(retrieve_one(index, target))
            for index, target in enumerate(targets)
        ]
        try:
            retrieved = await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        chunks_by_index: list[list[DocumentChunk]] = [[] for _ in targets]
        for index, target_chunks in retrieved:
            chunks_by_index[index] = target_chunks
        candidates = [chunk for target_chunks in chunks_by_index for chunk in target_chunks]
        return await cls._finalize_retrieval_chunks(
            request,
            preparation,
            candidates,
            models,
            log_id,
        )

    @classmethod
    async def _retrieve_single_target(
        cls,
        request: KnowledgeRetrievalRequest,
        target: RetrievalTarget,
        document_ids_include: list[str] | None,
        store: AsyncElasticSearchRetrieval,
        models: AsyncRetrievalModelGateway,
        *,
        use_request_reranker: bool,
        request_reranker: Any,
    ) -> list[DocumentChunk]:
        started_at = time.perf_counter()
        target_type = target.params.retrieve_type
        full_text_options = cls._search_options(
            target,
            request,
            document_ids_include,
            top_k=target.params.top_k if target_type == RetrieveType.PARTICIPLE else target.params.top_n,
            score_threshold=(
                None
                if target_type == RetrieveType.PARTICIPLE
                else target.params.similarity_threshold
            ),
        )
        if target_type == RetrieveType.PARTICIPLE:
            chunks = await store.search_by_full_text(request.query, full_text_options)
            cls._log_target_done(target, 0, len(chunks), len(chunks), len(chunks), started_at)
            return chunks

        vector_options = cls._search_options(
            target,
            request,
            document_ids_include,
            top_k=target.params.top_k if target_type == RetrieveType.SEMANTIC else target.params.top_n,
            score_threshold=target.params.vector_similarity_weight,
        )
        if target_type == RetrieveType.SEMANTIC:
            chunks = await store.search_by_vector(target.embedding, request.query, vector_options)
            cls._log_target_done(target, len(chunks), 0, len(chunks), len(chunks), started_at)
            return chunks

        vector_chunks, full_text_chunks = await asyncio.gather(
            store.search_by_vector(target.embedding, request.query, vector_options),
            store.search_by_full_text(request.query, full_text_options),
        )
        candidates = cls._deduplicate_chunks(vector_chunks + full_text_chunks)
        reranker = request_reranker if use_request_reranker else target.reranker
        if candidates and reranker:
            ranked = await models.rerank(
                reranker,
                request.query,
                candidates,
                target.params.top_k,
            )
        elif candidates and use_request_reranker:
            ranked = cls._apply_rerank_fallback(candidates, target.params.top_k)
        else:
            ranked = candidates[:target.params.top_k]
        chunks = [
            chunk
            for chunk in ranked
            if (chunk.metadata or {}).get("score", 0) > target.params.rerank_score_threshold
        ]
        cls._log_target_done(
            target,
            len(vector_chunks),
            len(full_text_chunks),
            len(candidates),
            len(chunks),
            started_at,
            local_rerank=True,
        )
        return chunks

    @classmethod
    async def _finalize_retrieval_chunks(
        cls,
        request: KnowledgeRetrievalRequest,
        preparation: RetrievalPreparation,
        chunks: list[DocumentChunk],
        models: AsyncRetrievalModelGateway,
        log_id: str,
    ) -> list[DocumentChunk]:
        candidates_count = len(chunks)
        unique_chunks = cls._deduplicate_chunks(chunks)
        if not unique_chunks:
            return []

        targets = preparation.targets
        single_hybrid_uses_request_rerank = (
            request.rerank_id is not None
            and len(targets) == 1
            and targets[0].params.retrieve_type == RetrieveType.HYBRID
        )
        needs_global_rerank = len(targets) > 1 or (
            request.rerank_id is not None and not single_hybrid_uses_request_rerank
        )
        if needs_global_rerank:
            reranker = (
                preparation.request_reranker
                if request.rerank_id is not None
                else targets[0].reranker
            )
            if reranker:
                ranked_chunks = await models.rerank(
                    reranker,
                    request.query,
                    unique_chunks,
                    request.top_k,
                )
            else:
                ranked_chunks = cls._apply_rerank_fallback(unique_chunks, request.top_k)
            threshold: float | None = cls._resolve_rerank_score_threshold(request)
            filtered_chunks = [
                chunk
                for chunk in ranked_chunks
                if (chunk.metadata or {}).get("score", 0) > threshold
            ]
        else:
            ranked_chunks = sorted(
                unique_chunks,
                key=lambda chunk: (chunk.metadata or {}).get("score", 0),
                reverse=True,
            )
            threshold = None
            filtered_chunks = ranked_chunks
        result = filtered_chunks[:request.top_k]
        logger.info(
            "[Retrieval] finalize %s",
            cls._format_log_fields(
                {
                    "id": log_id,
                    "candidates": candidates_count,
                    "deduped": len(unique_chunks),
                    "global_rerank": needs_global_rerank,
                    "threshold": threshold if threshold is not None else "none",
                    "result_count": len(result),
                    "async_mode": "native",
                }
            ),
        )
        return result

    @staticmethod
    def _search_options(
        target: RetrievalTarget,
        request: KnowledgeRetrievalRequest,
        document_ids_include: list[str] | None,
        *,
        top_k: int,
        score_threshold: float | None,
    ) -> RetrievalSearchOptions:
        return RetrievalSearchOptions(
            indices=target.index_name,
            top_k=top_k,
            score_threshold=score_threshold,
            file_names_filter=tuple(request.file_names_filter),
            document_ids_include=(
                tuple(document_ids_include) if document_ids_include is not None else None
            ),
            knn_num_candidates=None,
        )

    @classmethod
    def _finish_empty(
        cls,
        log_id: str,
        started_at: float,
        reason: str,
        snapshot_ms: int,
        metadata_llm_ms: int = 0,
        metadata_query_ms: int = 0,
    ) -> KnowledgeRetrievalResult:
        logger.info(
            "[Retrieval] finish %s",
            cls._format_log_fields(
                {
                    "id": log_id,
                    "reason": reason,
                    "target_count": 0,
                    "final_count": 0,
                    "db_snapshot_ms": snapshot_ms,
                    "metadata_llm_ms": metadata_llm_ms,
                    "metadata_query_ms": metadata_query_ms,
                    "elapsed_ms": cls._elapsed_ms(started_at),
                    "async_mode": "native",
                }
            ),
        )
        return KnowledgeRetrievalResult(chunks=[])

    @classmethod
    def _log_target_done(
        cls,
        target: RetrievalTarget,
        vector_count: int,
        full_text_count: int,
        merged_count: int,
        result_count: int,
        started_at: float,
        *,
        local_rerank: bool = False,
    ) -> None:
        logger.info(
            "[Retrieval] target_done %s",
            cls._format_log_fields(
                {
                    "kb_id": cls._compact_id(target.knowledge_id),
                    "index": target.index_name,
                    "type": target.params.retrieve_type,
                    "vector_kept": vector_count,
                    "fulltext_kept": full_text_count,
                    "merged": merged_count,
                    "local_rerank": local_rerank,
                    "result_count": result_count,
                    "elapsed_ms": cls._elapsed_ms(started_at),
                    "async_mode": "native",
                }
            ),
        )

    @classmethod
    def _log_metadata_filter(
        cls,
        log_id: str,
        request: KnowledgeRetrievalRequest,
        preparation: RetrievalPreparation,
        filter_groups: list[EngineFilterGroup],
        document_ids_include: list[str] | None,
    ) -> None:
        logger.info(
            "[Retrieval] metadata_filter %s",
            cls._format_log_fields(
                {
                    "id": log_id,
                    "mode": request.metadata_filter_mode,
                    "effective": bool(filter_groups),
                    "common_fields": len(preparation.common_metadata_defs),
                    "effective_groups": len(filter_groups),
                    "matched_documents": (
                        len(document_ids_include)
                        if document_ids_include is not None
                        else "none"
                    ),
                    "async_mode": "native",
                }
            ),
        )

    @staticmethod
    def _resolve_rerank_score_threshold(request: KnowledgeRetrievalRequest) -> float:
        if request.rerank_score_threshold is not None:
            return request.rerank_score_threshold
        if request.vector_similarity_weight is not None:
            return request.vector_similarity_weight
        return 0.1

    @staticmethod
    def _build_retrieval_params(
        request: KnowledgeRetrievalRequest,
        config: Any = None,
    ) -> RetrievalParams:
        """Keep configuration precedence available to compatibility callers."""

        return KnowledgeRetrievalPreparation._build_retrieval_params(request, config)

    @staticmethod
    def _resolve_global_score_threshold(
        request: KnowledgeRetrievalRequest,
        targets: Sequence[RetrievalTarget],
        used_rerank: bool,
    ) -> float:
        if used_rerank:
            return KnowledgeRetrievalService._resolve_rerank_score_threshold(request)
        retrieve_types = {target.params.retrieve_type for target in targets}
        if retrieve_types == {RetrieveType.PARTICIPLE}:
            return request.similarity_threshold
        if retrieve_types == {RetrieveType.SEMANTIC}:
            return request.vector_similarity_weight or 0.0
        return min(
            request.similarity_threshold,
            request.vector_similarity_weight or 0.0,
        )

    @staticmethod
    def _apply_rerank_fallback(
        chunks: Sequence[DocumentChunk],
        top_k: int,
    ) -> list[DocumentChunk]:
        fallback = list(chunks[:top_k])
        for chunk in fallback:
            if chunk.metadata is None:
                chunk.metadata = {}
            chunk.metadata.setdefault("score", 0.5)
        return fallback

    @staticmethod
    def _get_common_metadata_defs(
        metadata_defs_by_kb: dict[Any, dict[str, dict]],
    ) -> dict[str, dict]:
        field_names = set()
        for metadata_defs in metadata_defs_by_kb.values():
            field_names.update(metadata_defs.keys())

        common_defs: dict[str, dict] = {}
        for field_name in field_names:
            common_type = None
            common_def = None
            for metadata_defs in metadata_defs_by_kb.values():
                field_def = metadata_defs.get(field_name)
                if not field_def:
                    common_def = None
                    break
                if common_type is None:
                    common_type = field_def["type"]
                    common_def = field_def
                elif common_type != field_def["type"]:
                    common_def = None
                    break
            if common_def:
                common_defs[field_name] = dict(common_def)
        return common_defs

    @staticmethod
    def _build_common_filter_groups(
        metadata_filters: list[Any],
        common_fields: set[str],
    ) -> list[EngineFilterGroup]:
        filter_groups = []
        for group in metadata_filters:
            conditions = [
                EngineFilterCondition(
                    field=condition.field,
                    operator=condition.operator,
                    value=condition.value,
                )
                for condition in group.conditions
                if condition.field in common_fields
            ]
            if conditions:
                filter_groups.append(
                    EngineFilterGroup(conditions=conditions, logic=group.logic)
                )
        return filter_groups

    @staticmethod
    def _deduplicate_chunks(chunks: Sequence[DocumentChunk]) -> list[DocumentChunk]:
        seen_keys = set()
        result = []
        for chunk in chunks:
            metadata = chunk.metadata or {}
            doc_id = metadata.get("doc_id")
            document_id = metadata.get("document_id")
            sort_id = metadata.get("sort_id")
            if doc_id:
                dedupe_key = ("doc_id", doc_id)
            elif document_id is not None and sort_id is not None:
                dedupe_key = ("document_sort", document_id, sort_id)
            else:
                dedupe_key = ("content", hash(chunk.page_content))
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)
            result.append(chunk)
        return result

    @staticmethod
    def _include_document_ids(
        chunks: Sequence[DocumentChunk],
        document_ids_include: list[str] | None,
    ) -> list[DocumentChunk]:
        if document_ids_include is None:
            return list(chunks)
        include_ids = set(document_ids_include)
        return [
            chunk
            for chunk in chunks
            if (chunk.metadata or {}).get("document_id") in include_ids
        ]
