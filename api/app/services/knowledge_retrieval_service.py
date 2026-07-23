import asyncio
import logging
import time
import uuid
from enum import Enum
from typing import Any, Sequence

from langchain_core.documents import Document

from app.core.config import settings
from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.models import (
    RedBearEmbeddings,
    RedBearLLM,
    RedBearModelConfig,
    RedBearRerank,
)
from app.core.rag.knowledge_graph.config import GraphPipeline
from app.core.rag.knowledge_graph.elasticsearch_store import GraphElasticsearchStore
from app.core.rag.knowledge_graph.models import (
    GraphIndexRuntime,
    GraphRetrievalRequest,
)
from app.core.rag.knowledge_graph.retrieval_pipeline import (
    KnowledgeGraphRetrievalPipeline,
)
from app.core.rag.metadata.filter_engine import (
    FilterCondition as EngineFilterCondition,
    FilterGroup as EngineFilterGroup,
)
from app.core.rag.models.chunk import DocumentChunk, chunk_retrieval_content
from app.core.rag.retrieval.async_elasticsearch import (
    AsyncElasticSearchRetrieval,
    AsyncElasticsearchClientProvider,
)
from app.core.rag.retrieval.graph_bridge import GraphRetrievalBridge
from app.core.rag.retrieval.models import (
    GraphTargetSnapshot,
    ModelRuntimeSnapshot,
    RetrievalParams,
    RetrievalPreparation,
    RetrievalPrincipal,
    RetrievalSearchOptions,
    RetrievalTarget,
    RetrievalTimings,
)
from app.models.models_model import ModelType
from app.schemas.chunk_schema import RetrieveType
from app.schemas.knowledge_metadata_schema import MetadataFilterMode
from app.schemas.knowledge_retrieval_schema import (
    KnowledgeRetrievalRequest,
    KnowledgeRetrievalResult,
)
from app.services.knowledge_retrieval_preparation import KnowledgeRetrievalPreparation
from app.services.metadata_auto_filter_service import MetadataAutoFilterService

logger = logging.getLogger(__name__)

ModelApiKeySnapshot = ModelRuntimeSnapshot
_RERANK_SOURCE_INDEX = "_retrieval_source_index"


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

    @staticmethod
    def _model_config(
        snapshot: ModelRuntimeSnapshot,
        *,
        extra_params: dict[str, Any] | None = None,
    ) -> RedBearModelConfig:
        """Map a request-local snapshot to the shared model configuration."""

        return RedBearModelConfig(
            model_name=snapshot.model_name,
            provider=snapshot.provider,
            api_key=snapshot.api_key,
            base_url=snapshot.api_base,
            capability=list(snapshot.capability),
            is_omni=snapshot.is_omni,
            extra_params=dict(extra_params or {}),
        )

    @classmethod
    def _metadata_llm(
        cls,
        snapshot: ModelRuntimeSnapshot,
        *,
        extra_params: dict[str, Any] | None = None,
    ) -> RedBearLLM:
        model_type = ModelType.LLM
        if snapshot.model_type in {ModelType.LLM.value, ModelType.CHAT.value}:
            model_type = ModelType(snapshot.model_type)
        return RedBearLLM(
            cls._model_config(snapshot, extra_params=extra_params),
            type=model_type,
        )

    @classmethod
    async def _rerank_with_shared_model(
        cls,
        snapshot: ModelRuntimeSnapshot,
        query: str,
        chunks: Sequence[DocumentChunk],
        top_k: int,
    ) -> list[DocumentChunk]:
        if top_k <= 0 or not chunks:
            return []

        try:
            documents = [
                Document(
                    page_content=chunk_retrieval_content(chunk),
                    metadata={
                        **(chunk.metadata or {}),
                        _RERANK_SOURCE_INDEX: index,
                    },
                )
                for index, chunk in enumerate(chunks)
            ]
            reranker = RedBearRerank(
                cls._model_config(snapshot, extra_params={"top_n": top_k})
            )
            reranked_documents = list(
                await reranker.acompress_documents(documents, query)
            )
            reranked_documents.sort(
                key=lambda item: item.metadata.get("relevance_score", 0),
                reverse=True,
            )

            result: list[DocumentChunk] = []
            for item in reranked_documents[:top_k]:
                source_index = item.metadata.get(_RERANK_SOURCE_INDEX)
                if (
                    not isinstance(source_index, int)
                    or isinstance(source_index, bool)
                    or not 0 <= source_index < len(chunks)
                ):
                    continue
                chunk = chunks[source_index]
                if chunk.metadata is None:
                    chunk.metadata = {}
                chunk.metadata["score"] = item.metadata.get("relevance_score", 0)
                result.append(chunk)
            return result
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "[Retrieval] shared rerank failed; using retrieval order provider=%s error_type=%s",
                snapshot.provider,
                type(exc).__name__,
            )
            return cls._apply_rerank_fallback(chunks, top_k)

    @classmethod
    def _build_retrieval_start_log_fields(
        cls,
        log_id: str,
        request: KnowledgeRetrievalRequest,
        principal: RetrievalPrincipal | None,
        timings: RetrievalTimings,
    ) -> dict[str, Any]:
        fields: dict[str, Any] = {
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
            "enable_graph_retrieval": request.enable_graph_retrieval,
            "metadata_mode": request.metadata_filter_mode,
            "async_mode": "native",
        }
        fields.update(timings.as_log_fields())
        return fields

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
        timings = RetrievalTimings()
        logger.info(
            "[Retrieval] start %s",
            cls._format_log_fields(
                cls._build_retrieval_start_log_fields(log_id, request, principal, timings)
            ),
        )

        snapshot_started_at = time.perf_counter()
        preparation = await KnowledgeRetrievalPreparation.prepare(request, principal)
        timings.db_snapshot_ms = cls._elapsed_ms(snapshot_started_at)
        if not preparation.targets:
            return cls._finish_empty(log_id, started_at, "no_targets", timings)

        metadata_started_at = time.perf_counter()
        filter_groups = await cls._build_metadata_filter_groups(
            request,
            preparation,
        )
        timings.metadata_llm_ms = cls._elapsed_ms(metadata_started_at)

        metadata_query_started_at = time.perf_counter()
        document_ids_include = await KnowledgeRetrievalPreparation.resolve_metadata_document_ids(
            preparation,
            filter_groups,
        )
        timings.metadata_query_ms = cls._elapsed_ms(metadata_query_started_at)
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
                timings,
            )

        client = await AsyncElasticsearchClientProvider.get_shared_client()
        store = AsyncElasticSearchRetrieval(client, timings)
        retrieval_result = await cls._retrieve_targets(
            request,
            preparation,
            document_ids_include,
            store,
            log_id,
            timings,
        )
        chunks = retrieval_result.chunks
        if (
            preparation.graph
            and preparation.graph.pipeline is GraphPipeline.LEGACY
        ):
            graph_document = await GraphRetrievalBridge.retrieve(preparation.graph, timings)
            if graph_document:
                chunks.insert(0, graph_document)

        chunks = cls._include_document_ids(chunks, document_ids_include)
        graph_context_chunks = cls._build_graph_context_chunks(
            retrieval_result.entities,
            retrieval_result.relationships,
        )
        if graph_context_chunks:
            chunks = graph_context_chunks + chunks
        logger.info(
            "[Retrieval] finish %s",
            cls._format_log_fields(
                {
                    "id": log_id,
                    "reason": "ok",
                    "target_count": len(preparation.targets),
                    "document_filter_count": len(document_ids_include or []),
                    "graph_context_count": len(graph_context_chunks),
                    "final_count": len(chunks),
                    "elapsed_ms": cls._elapsed_ms(started_at),
                    "async_mode": "native",
                }
                | timings.as_log_fields()
            ),
        )
        return KnowledgeRetrievalResult(
            chunks=chunks,
            entities=retrieval_result.entities,
            relationships=retrieval_result.relationships,
        )

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
            return await MetadataAutoFilterService.generate_filter_groups_async(
                query=request.query,
                metadata_defs=dict(preparation.common_metadata_defs),
                llm=cls._metadata_llm(preparation.metadata_llm),
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
        log_id: str,
        timings: RetrievalTimings | None = None,
    ) -> KnowledgeRetrievalResult:
        targets = preparation.targets
        if not targets:
            return KnowledgeRetrievalResult()

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
                | cls._timing_log_fields(timings)
            ),
        )
        semaphore = asyncio.Semaphore(max_workers)
        graph_targets_by_knowledge_id = (
            {
                graph_target.knowledge_id: graph_target
                for graph_target in preparation.graph.targets
            }
            if preparation.graph is not None
            else {}
        )

        async def retrieve_one(
            index: int,
            target: RetrievalTarget,
        ) -> tuple[int, KnowledgeRetrievalResult]:
            async with semaphore:
                result = await cls._retrieve_single_target(
                    request,
                    target,
                    document_ids_include,
                    store,
                    use_request_reranker=(
                        request.rerank_id is not None
                        and len(targets) == 1
                        and target.params.retrieve_type == RetrieveType.HYBRID
                    ),
                    request_reranker=preparation.request_reranker,
                    log_id=log_id,
                    graph_target=graph_targets_by_knowledge_id.get(
                        target.knowledge_id
                    ),
                    timings=timings,
                )
                return index, result

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
        graph_entities: list[Any] = []
        graph_relationships: list[Any] = []
        for index, target_result in retrieved:
            chunks_by_index[index] = target_result.chunks
            graph_entities.extend(target_result.entities)
            graph_relationships.extend(target_result.relationships)
        evidence_graph_only = (
            preparation.graph is not None
            and preparation.graph.pipeline is GraphPipeline.EVIDENCE
            and all(
                target.params.retrieve_type == RetrieveType.Graph
                for target in targets
            )
        )
        candidates = (
            cls._round_robin_chunk_groups(chunks_by_index)
            if evidence_graph_only
            else [
                chunk
                for target_chunks in chunks_by_index
                for chunk in target_chunks
            ]
        )
        chunks = await cls._finalize_retrieval_chunks(
            request,
            preparation,
            candidates,
            log_id,
            timings,
        )
        graph_entities = cls._deduplicate_graph_items(
            graph_entities,
            "entity_key",
        )
        graph_relationships = cls._deduplicate_graph_items(
            graph_relationships,
            "relation_key",
        )
        graph_entities = cls._filter_graph_items_by_chunk_paths(
            graph_entities,
            chunks,
            "entity_key",
            "entity",
        )
        graph_relationships = cls._filter_graph_items_by_chunk_paths(
            graph_relationships,
            chunks,
            "relation_key",
            "relation",
        )
        return KnowledgeRetrievalResult(
            chunks=chunks,
            entities=graph_entities,
            relationships=graph_relationships,
        )

    @staticmethod
    def _round_robin_chunk_groups(
        groups: Sequence[Sequence[DocumentChunk]],
    ) -> list[DocumentChunk]:
        positions = [0 for _ in groups]
        result: list[DocumentChunk] = []
        while True:
            progressed = False
            for index, group in enumerate(groups):
                if positions[index] >= len(group):
                    continue
                result.append(group[positions[index]])
                positions[index] += 1
                progressed = True
            if not progressed:
                break
        return result

    @classmethod
    async def _retrieve_single_target(
        cls,
        request: KnowledgeRetrievalRequest,
        target: RetrievalTarget,
        document_ids_include: list[str] | None,
        store: AsyncElasticSearchRetrieval,
        *,
        use_request_reranker: bool,
        request_reranker: Any,
        log_id: str | None = None,
        graph_target: GraphTargetSnapshot | None = None,
        timings: RetrievalTimings | None = None,
    ) -> KnowledgeRetrievalResult:
        started_at = time.perf_counter()
        target_type = target.params.retrieve_type
        if (
            target_type == RetrieveType.Graph
            and graph_target is not None
            and graph_target.pipeline is GraphPipeline.EVIDENCE
        ):
            return await cls._retrieve_evidence_graph_target(
                request,
                target,
                graph_target,
                document_ids_include,
                store,
                started_at,
                timings,
                log_id,
            )

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
            cls._log_target_done(target, 0, len(chunks), len(chunks), len(chunks), started_at, timings=timings)
            return KnowledgeRetrievalResult(chunks=chunks)

        vector_options = cls._search_options(
            target,
            request,
            document_ids_include,
            top_k=target.params.top_k if target_type == RetrieveType.SEMANTIC else target.params.top_n,
            score_threshold=target.params.vector_similarity_weight,
        )
        embedding = RedBearEmbeddings(cls._model_config(target.embedding))
        if target_type == RetrieveType.SEMANTIC:
            chunks = await store.search_by_vector(embedding, request.query, vector_options)
            cls._log_target_done(target, len(chunks), 0, len(chunks), len(chunks), started_at, timings=timings)
            return KnowledgeRetrievalResult(chunks=chunks)

        vector_task = asyncio.create_task(
            store.search_by_vector(embedding, request.query, vector_options)
        )
        full_text_task = asyncio.create_task(
            store.search_by_full_text(request.query, full_text_options)
        )
        try:
            vector_chunks, full_text_chunks = await asyncio.gather(
                vector_task,
                full_text_task,
            )
        except BaseException:
            vector_task.cancel()
            full_text_task.cancel()
            await asyncio.gather(
                vector_task,
                full_text_task,
                return_exceptions=True,
            )
            raise
        candidates = cls._deduplicate_chunks(vector_chunks + full_text_chunks)
        reranker = request_reranker if use_request_reranker else target.reranker
        local_rerank_started_at = time.perf_counter()
        try:
            if candidates and reranker:
                ranked = await cls._rerank_with_shared_model(
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
        finally:
            cls._record_timing(timings, "local_rerank_ms", local_rerank_started_at)
        cls._log_target_done(
            target,
            len(vector_chunks),
            len(full_text_chunks),
            len(candidates),
            len(chunks),
            started_at,
            local_rerank=True,
            timings=timings,
        )
        return KnowledgeRetrievalResult(chunks=chunks)

    @classmethod
    async def _retrieve_evidence_graph_target(
        cls,
        request: KnowledgeRetrievalRequest,
        target: RetrievalTarget,
        graph_target: GraphTargetSnapshot,
        document_ids_include: list[str] | None,
        store: AsyncElasticSearchRetrieval,
        started_at: float,
        timings: RetrievalTimings | None,
        log_id: str | None,
    ) -> KnowledgeRetrievalResult:
        if graph_target.knowledge_id != target.knowledge_id:
            raise ValueError("graph target does not match retrieval target")

        graph_started_at = time.perf_counter()
        try:
            client = await AsyncElasticsearchClientProvider.get_shared_client()
            graph_store = GraphElasticsearchStore(client)
            llm_type = (
                ModelType.CHAT
                if graph_target.llm.model_type == ModelType.CHAT.value
                else ModelType.LLM
            )
            llm = RedBearLLM(
                cls._model_config(
                    graph_target.llm,
                    extra_params={"temperature": 0},
                ),
                type=llm_type,
            )
            embedding = RedBearEmbeddings(
                cls._model_config(graph_target.embedding)
            )
            pipeline = KnowledgeGraphRetrievalPipeline(
                graph_store,
                llm,
                embedding,
                store.resolve_parent_chunks,
            )
            graph_result = await pipeline.retrieve_with_graph_data(
                GraphRetrievalRequest(
                    query=request.query,
                    runtime=GraphIndexRuntime(
                        knowledge_id=str(graph_target.knowledge_id),
                        workspace_id=str(graph_target.workspace_id),
                        graph_index_name=graph_target.graph_index_name,
                        chunk_index_name=graph_target.chunk_index_name,
                        entity_types=(),
                        scene_name="",
                        llm=graph_target.llm,
                        embedding=graph_target.embedding,
                    ),
                    allowed_document_ids=(
                        tuple(document_ids_include)
                        if document_ids_include is not None
                        else None
                    ),
                    file_names=tuple(request.file_names_filter),
                    entity_top_n=settings.KNOWLEDGE_GRAPH_ENTITY_TOP_N,
                    relation_top_n=settings.KNOWLEDGE_GRAPH_RELATION_TOP_N,
                    neighbor_top_n=settings.KNOWLEDGE_GRAPH_NEIGHBOR_TOP_N,
                    entity_similarity_threshold=(
                        settings.KNOWLEDGE_GRAPH_ENTITY_SIMILARITY_THRESHOLD
                    ),
                    relation_similarity_threshold=(
                        settings.KNOWLEDGE_GRAPH_RELATION_SIMILARITY_THRESHOLD
                    ),
                    related_chunk_number=(
                        settings.KNOWLEDGE_GRAPH_RELATED_CHUNK_NUMBER
                    ),
                    max_candidates=max(
                        settings.KNOWLEDGE_GRAPH_MAX_CANDIDATES,
                        request.top_k,
                        target.params.top_k,
                    ),
                    max_paths_per_chunk=(
                        settings.KNOWLEDGE_GRAPH_MAX_PATHS_PER_CHUNK
                    ),
                )
            )
            chunks = graph_result.chunks
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            stage = "timeout" if isinstance(exc, TimeoutError) else "pipeline"
            logger.warning(
                "[Retrieval] graph_target_failed"
                " id=%s kb_id=%s stage=%s error_type=%s elapsed_ms=%d",
                log_id or "unknown",
                cls._compact_id(target.knowledge_id),
                stage,
                type(exc).__name__,
                cls._elapsed_ms(graph_started_at),
            )
            graph_result = None
            chunks = []
        finally:
            cls._record_timing(timings, "graph_ms", graph_started_at)

        cls._log_target_done(
            target,
            0,
            0,
            len(chunks),
            len(chunks),
            started_at,
            timings=timings,
        )
        if graph_result is None:
            return KnowledgeRetrievalResult(chunks=chunks)
        return KnowledgeRetrievalResult(
            chunks=chunks,
            entities=graph_result.entities,
            relationships=graph_result.relationships,
        )

    @classmethod
    async def _finalize_retrieval_chunks(
        cls,
        request: KnowledgeRetrievalRequest,
        preparation: RetrievalPreparation,
        chunks: list[DocumentChunk],
        log_id: str,
        timings: RetrievalTimings | None = None,
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
        evidence_graph_only = (
            preparation.graph is not None
            and preparation.graph.pipeline is GraphPipeline.EVIDENCE
            and all(
                target.params.retrieve_type == RetrieveType.Graph
                for target in targets
            )
        )
        needs_global_rerank = not evidence_graph_only and (
            len(targets) > 1
            or (
                request.rerank_id is not None
                and not single_hybrid_uses_request_rerank
            )
        )
        if needs_global_rerank:
            global_rerank_started_at = time.perf_counter()
            try:
                reranker = (
                    preparation.request_reranker
                    if request.rerank_id is not None
                    else targets[0].reranker
                )
                if reranker:
                    ranked_chunks = await cls._rerank_with_shared_model(
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
            finally:
                cls._record_timing(timings, "global_rerank_ms", global_rerank_started_at)
        elif evidence_graph_only:
            ranked_chunks = sorted(
                unique_chunks,
                key=lambda chunk: (chunk.metadata or {}).get("score", 0),
                reverse=True,
            )
            threshold = None
            filtered_chunks = ranked_chunks
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
                | cls._timing_log_fields(timings)
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
        timings: RetrievalTimings,
    ) -> KnowledgeRetrievalResult:
        logger.info(
            "[Retrieval] finish %s",
            cls._format_log_fields(
                {
                    "id": log_id,
                    "reason": reason,
                    "target_count": 0,
                    "final_count": 0,
                    "elapsed_ms": cls._elapsed_ms(started_at),
                    "async_mode": "native",
                }
                | timings.as_log_fields()
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
        timings: RetrievalTimings | None = None,
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
                | cls._timing_log_fields(timings)
            ),
        )

    @staticmethod
    def _timing_log_fields(timings: RetrievalTimings | None) -> dict[str, int]:
        return timings.as_log_fields() if timings is not None else RetrievalTimings().as_log_fields()

    @staticmethod
    def _record_timing(
        timings: RetrievalTimings | None,
        field_name: str,
        started_at: float,
    ) -> None:
        if timings is None:
            return
        setattr(
            timings,
            field_name,
            getattr(timings, field_name) + KnowledgeRetrievalService._elapsed_ms(started_at),
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
    def _deduplicate_graph_items(
        items: Sequence[Any],
        key_field: str,
    ) -> list[Any]:
        seen_keys: set[str] = set()
        result: list[Any] = []
        for item in items:
            if isinstance(item, dict):
                key = str(item.get(key_field) or "")
            else:
                key = str(getattr(item, key_field, "") or "")
            if not key or key in seen_keys:
                continue
            seen_keys.add(key)
            result.append(item)
        return result

    @classmethod
    def _build_graph_context_chunks(
        cls,
        entities: Sequence[Any],
        relationships: Sequence[Any],
    ) -> list[DocumentChunk]:
        chunks: list[DocumentChunk] = []
        if entities:
            chunks.append(cls._graph_entities_to_chunk(entities))
        if relationships:
            chunks.append(cls._graph_relationships_to_chunk(relationships))
        return chunks

    @classmethod
    def _graph_entities_to_chunk(
        cls,
        entities: Sequence[Any],
    ) -> DocumentChunk:
        source_chunk_ids = cls._graph_items_unique_list(entities, "source_chunk_ids")
        return DocumentChunk(
            page_content="\n".join(
                ["Graph entities:"]
                + [
                    cls._format_graph_entity_line(entity, index)
                    for index, entity in enumerate(entities, start=1)
                ]
            ),
            metadata={
                "doc_id": "graph_entities",
                "chunk_type": "graph_entities",
                "retrieval_source": "graph",
                "graph_item_type": "entities",
                "score": 1,
                "graph_score": 1,
                "graph_item_count": len(entities),
                "entities": cls._graph_items_payload(entities),
                "source_chunk_ids": source_chunk_ids,
            },
        )

    @classmethod
    def _graph_relationships_to_chunk(
        cls,
        relationships: Sequence[Any],
    ) -> DocumentChunk:
        source_chunk_ids = cls._graph_items_unique_list(
            relationships,
            "source_chunk_ids",
        )
        return DocumentChunk(
            page_content="\n".join(
                ["Graph relationships:"]
                + [
                    cls._format_graph_relationship_line(relationship, index)
                    for index, relationship in enumerate(relationships, start=1)
                ]
            ),
            metadata={
                "doc_id": "graph_relationships",
                "chunk_type": "graph_relationships",
                "retrieval_source": "graph",
                "graph_item_type": "relationships",
                "score": 1,
                "graph_score": 1,
                "graph_item_count": len(relationships),
                "relationships": cls._graph_items_payload(relationships),
                "source_chunk_ids": source_chunk_ids,
            },
        )

    @classmethod
    def _format_graph_entity_line(
        cls,
        entity: Any,
        index: int,
    ) -> str:
        entity_key = cls._graph_item_text(entity, "entity_key")
        entity_name = cls._graph_item_text(entity, "entity_name") or entity_key
        entity_type = cls._graph_item_text(entity, "entity_type")
        description = cls._graph_item_text(entity, "description")
        aliases = cls._graph_item_list(entity, "aliases")
        parts = [f"{index}. {entity_name or entity_key or 'unknown'}"]
        if entity_type:
            parts.append(f"type={entity_type}")
        if aliases:
            parts.append(f"aliases={', '.join(aliases)}")
        if description:
            parts.append(description)
        return " | ".join(parts)

    @classmethod
    def _format_graph_relationship_line(
        cls,
        relationship: Any,
        index: int,
    ) -> str:
        relation_key = cls._graph_item_text(relationship, "relation_key")
        from_name = (
            cls._graph_item_text(relationship, "from_entity_name")
            or cls._graph_item_text(relationship, "from_entity_key")
            or cls._graph_item_text(relationship, "src_id")
        )
        to_name = (
            cls._graph_item_text(relationship, "to_entity_name")
            or cls._graph_item_text(relationship, "to_entity_key")
            or cls._graph_item_text(relationship, "tgt_id")
        )
        predicate = cls._graph_item_text(relationship, "predicate")
        label = cls._graph_item_text(relationship, "label")
        description = cls._graph_item_text(relationship, "description")
        keywords = cls._graph_item_list(relationship, "keywords")
        source_chunk_ids = cls._graph_item_list(relationship, "source_chunk_ids")
        connector = (
            "->"
            if cls._graph_item_value(relationship, "directed") is not False
            else "--"
        )
        endpoints = (
            f"{from_name} {connector} {to_name}"
            if from_name and to_name
            else from_name or to_name
        )
        parts = [f"Relationship: {endpoints or relation_key}"]
        relation_label = predicate or label
        if relation_label:
            parts.append(f"Label: {relation_label}")
        if keywords:
            parts.append(f"Keywords: {', '.join(keywords)}")
        if description:
            parts.append(f"Description: {description}")
        return f"{index}. " + " | ".join(parts)

    @staticmethod
    def _graph_items_payload(items: Sequence[Any]) -> list[Any]:
        result: list[Any] = []
        for item in items:
            if isinstance(item, dict):
                result.append(dict(item))
            elif hasattr(item, "model_dump"):
                result.append(item.model_dump())
            else:
                result.append({"value": str(item)})
        return result

    @classmethod
    def _graph_items_unique_list(
        cls,
        items: Sequence[Any],
        key: str,
    ) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for item in items:
            for value in cls._graph_item_list(item, key):
                if value in seen:
                    continue
                seen.add(value)
                result.append(value)
        return result

    @staticmethod
    def _graph_item_value(item: Any, key: str) -> Any:
        if isinstance(item, dict):
            return item.get(key)
        return getattr(item, key, None)

    @classmethod
    def _graph_item_text(cls, item: Any, key: str) -> str:
        value = cls._graph_item_value(item, key)
        return str(value).strip() if value is not None else ""

    @classmethod
    def _graph_item_list(cls, item: Any, key: str) -> list[str]:
        value = cls._graph_item_value(item, key)
        if value is None:
            return []
        if isinstance(value, (list, tuple, set)):
            return [str(item).strip() for item in value if str(item).strip()]
        text = str(value).strip()
        return [text] if text else []

    @classmethod
    def _filter_graph_items_by_chunk_paths(
        cls,
        items: Sequence[Any],
        chunks: Sequence[DocumentChunk],
        key_field: str,
        projection_type: str,
    ) -> list[Any]:
        keys = cls._graph_projection_keys_from_chunks(chunks, projection_type)
        if not keys:
            return list(items)
        return [
            item
            for item in items
            if cls._graph_item_key(item, key_field) in keys
        ]

    @staticmethod
    def _graph_projection_keys_from_chunks(
        chunks: Sequence[DocumentChunk],
        projection_type: str,
    ) -> set[str]:
        keys: set[str] = set()
        for chunk in chunks:
            match_paths = (chunk.metadata or {}).get("match_paths") or []
            if not isinstance(match_paths, list):
                continue
            for path in match_paths:
                if not isinstance(path, dict):
                    continue
                if path.get("evidence_type") != projection_type:
                    continue
                key = str(path.get("evidence_key") or "")
                if key:
                    keys.add(key)
        return keys

    @staticmethod
    def _graph_item_key(item: Any, key_field: str) -> str:
        if isinstance(item, dict):
            return str(item.get(key_field) or "")
        return str(getattr(item, key_field, "") or "")

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
