"""Native asynchronous knowledge retrieval with legacy response semantics."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Sequence
from enum import Enum
from typing import Any

from langchain_core.documents import Document as LangChainDocument
from redbear_model.runtime import RedBearEmbeddings, RedBearLLM, RedBearRerank

from ..api.dependencies import Principal
from ..api.schemas.chunk import RetrieveType
from ..api.schemas.knowledge_metadata import MetadataFilterMode
from ..api.schemas.knowledge_retrieval import (
    KnowledgeRetrievalRequest,
    KnowledgeRetrievalResult,
)
from ..errors import KnowledgeError
from ..rag.knowledge_graph.config import GraphPipeline
from ..rag.metadata.auto_filter import generate_filter_groups
from ..rag.metadata.filter_engine import FilterCondition, FilterGroup
from ..rag.models.chunk import DocumentChunk, chunk_retrieval_content
from ..rag.retrieval.async_elasticsearch import AsyncElasticSearchRetrieval
from ..rag.retrieval.elasticsearch_queries import (
    build_filter_clauses,
    build_full_text_query,
    build_vector_script_query,
    full_text_hits_to_chunks,
    normalize_vector,
    vector_hits_to_chunks,
)
from ..rag.retrieval.graph_bridge import GraphRetrievalBridge
from ..rag.retrieval.models import (
    GraphRetrievalSnapshot,
    GraphTargetSnapshot,
    ModelRuntimeSnapshot,
    RetrievalPreparation,
    RetrievalSearchOptions,
    RetrievalTarget,
    RetrievalTimings,
)
from ..runtime import ProcessRuntime
from .knowledge_retrieval_preparation import KnowledgeRetrievalPreparation

logger = logging.getLogger(__name__)
_SOURCE_INDEX = "_retrieval_source_index"
_MAX_RETRIEVAL_WORKERS = 3


def _record_elapsed(
    timings: RetrievalTimings | None,
    field: str,
    started_at: float,
) -> None:
    if timings is None:
        return
    elapsed_ms = max(0, int((time.perf_counter() - started_at) * 1000))
    setattr(timings, field, getattr(timings, field) + elapsed_ms)


class _TimedElasticSearchRetrieval(AsyncElasticSearchRetrieval):
    """Record oracle-compatible phases without owning another ES client."""

    def __init__(self, client: Any, timings: RetrievalTimings | None) -> None:
        self._timings = timings
        super().__init__(client)

    async def search_by_vector(
        self,
        embedding: Any,
        query: str,
        options: RetrievalSearchOptions,
    ) -> list[DocumentChunk]:
        embedding_started_at = time.perf_counter()
        try:
            vector = normalize_vector(await embedding.aembed_query(query))
        finally:
            _record_elapsed(self._timings, "embedding_ms", embedding_started_at)
        search_started_at = time.perf_counter()
        try:
            response = await self.client.search(
                index=options.indices,
                from_=0,
                size=options.top_k,
                query=build_vector_script_query(
                    vector,
                    build_filter_clauses(
                        options.file_names_filter,
                        options.document_ids_include,
                        require_vector=True,
                    ),
                ),
                allow_partial_search_results=False,
            )
        finally:
            _record_elapsed(self._timings, "es_vector_ms", search_started_at)
        return await self.resolve_parent_chunks(
            vector_hits_to_chunks(response, options.score_threshold),
            options.indices,
        )

    async def search_by_full_text(
        self,
        query: str,
        options: RetrievalSearchOptions,
    ) -> list[DocumentChunk]:
        search_started_at = time.perf_counter()
        try:
            response = await self.client.search(
                index=options.indices,
                from_=0,
                size=options.top_k,
                query=build_full_text_query(
                    query,
                    options.file_names_filter,
                    options.document_ids_include,
                ),
                allow_partial_search_results=False,
            )
        finally:
            _record_elapsed(self._timings, "es_fulltext_ms", search_started_at)
        return await self.resolve_parent_chunks(
            full_text_hits_to_chunks(response, options.score_threshold),
            options.indices,
        )

    async def resolve_parent_chunks(
        self,
        chunks: list[DocumentChunk],
        index: str,
    ) -> list[DocumentChunk]:
        has_parent = any(
            (chunk.metadata or {}).get("chunk_type") == "child"
            and (chunk.metadata or {}).get("parent_id")
            for chunk in chunks
        )
        if not has_parent:
            return chunks
        started_at = time.perf_counter()
        try:
            return await super().resolve_parent_chunks(chunks, index)
        finally:
            _record_elapsed(self._timings, "parent_resolution_ms", started_at)


class KnowledgeRetrievalService:
    @classmethod
    async def retrieve_async(
        cls,
        runtime: ProcessRuntime,
        request: KnowledgeRetrievalRequest,
        principal: Principal,
    ) -> KnowledgeRetrievalResult:
        log_id = cls._new_retrieval_log_id()
        started_at = time.perf_counter()
        timings = RetrievalTimings()
        logger.info(
            "[Retrieval] start %s",
            cls._format_log_fields(
                cls._build_retrieval_start_log_fields(
                    log_id,
                    request,
                    principal,
                    timings,
                )
            ),
        )
        snapshot_started_at = time.perf_counter()
        async with runtime.database.async_session() as db:
            preparation = await KnowledgeRetrievalPreparation.prepare_with_db(
                db,
                request,
                principal,
            )
        cls._record_timing(timings, "db_snapshot_ms", snapshot_started_at)
        if not preparation.targets:
            return cls._finish_empty(log_id, started_at, "no_targets", timings)

        metadata_started_at = time.perf_counter()
        filter_groups = await cls._build_metadata_filter_groups(
            runtime,
            request,
            preparation,
        )
        cls._record_timing(timings, "metadata_llm_ms", metadata_started_at)
        metadata_query_started_at = time.perf_counter()
        async with runtime.database.async_session() as db:
            document_ids = await KnowledgeRetrievalPreparation.resolve_metadata_document_ids(
                db,
                preparation,
                filter_groups,
            )
        cls._record_timing(timings, "metadata_query_ms", metadata_query_started_at)
        cls._log_metadata_filter(
            log_id,
            request,
            preparation,
            filter_groups,
            document_ids,
        )
        if document_ids == []:
            return cls._finish_empty(
                log_id,
                started_at,
                "metadata_filter_empty",
                timings,
            )

        client = await runtime.elasticsearch.client()
        store = _TimedElasticSearchRetrieval(client, timings)
        retrieval_result = await cls._retrieve_prepared(
            runtime,
            client,
            store,
            request,
            preparation,
            document_ids,
            timings=timings,
            log_id=log_id,
        )
        chunks = cls._include_document_ids(retrieval_result.chunks, document_ids)
        graph_context_chunks = cls._build_graph_context_chunks(
            retrieval_result.entities,
            retrieval_result.relationships,
        )
        chunks = graph_context_chunks + chunks
        logger.info(
            "[Retrieval] finish %s",
            cls._format_log_fields(
                {
                    "id": log_id,
                    "reason": "ok",
                    "target_count": len(preparation.targets),
                    "document_filter_count": len(document_ids or []),
                    "graph_context_count": len(graph_context_chunks),
                    "final_count": len(chunks),
                    "elapsed_ms": cls._elapsed_ms(started_at),
                    "async_mode": "native",
                }
                | timings.as_log_fields()
            ),
        )
        return KnowledgeRetrievalResult(chunks=chunks)

    @classmethod
    async def _retrieve_prepared(
        cls,
        runtime: ProcessRuntime,
        client: Any,
        store: AsyncElasticSearchRetrieval,
        request: KnowledgeRetrievalRequest,
        preparation: RetrievalPreparation,
        document_ids: list[str] | None,
        *,
        timings: RetrievalTimings | None = None,
        log_id: str | None = None,
    ) -> KnowledgeRetrievalResult:
        targets = preparation.targets
        if not targets:
            return KnowledgeRetrievalResult()

        graph_targets = (
            {target.knowledge_id: target for target in preparation.graph.targets}
            if preparation.graph is not None
            else {}
        )
        semaphore = asyncio.Semaphore(min(len(targets), _MAX_RETRIEVAL_WORKERS))
        logger.info(
            "[Retrieval] targets %s",
            cls._format_log_fields(
                {
                    "id": log_id or "unknown",
                    "target_count": len(targets),
                    "max_workers": min(len(targets), _MAX_RETRIEVAL_WORKERS),
                    "target_kbs": cls._compact_ids(
                        [target.knowledge_id for target in targets]
                    ),
                    "async_mode": "native",
                }
                | cls._timing_log_fields(timings)
            ),
        )

        async def retrieve_one(
            index: int,
            target: RetrievalTarget,
        ) -> tuple[int, KnowledgeRetrievalResult]:
            async with semaphore:
                result = await cls._retrieve_target(
                    runtime,
                    client,
                    store,
                    request,
                    target,
                    document_ids,
                    graph_target=graph_targets.get(target.knowledge_id),
                    use_request_reranker=(
                        request.rerank_id is not None
                        and len(targets) == 1
                        and target.params.retrieve_type is RetrieveType.HYBRID
                    ),
                    request_reranker=preparation.request_reranker,
                    timings=timings,
                    log_id=log_id,
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
        entities: list[Any] = []
        relationships: list[Any] = []
        for index, result in retrieved:
            chunks_by_index[index] = result.chunks
            entities.extend(result.entities)
            relationships.extend(result.relationships)

        evidence_graph_only = (
            preparation.graph is not None
            and preparation.graph.pipeline is GraphPipeline.EVIDENCE
            and all(target.params.retrieve_type is RetrieveType.Graph for target in targets)
        )
        candidates = (
            cls._round_robin_chunk_groups(chunks_by_index)
            if evidence_graph_only
            else [chunk for group in chunks_by_index for chunk in group]
        )
        chunks = await cls._finalize_retrieval_chunks(
            runtime,
            request,
            preparation,
            candidates,
            timings=timings,
            log_id=log_id,
        )
        entities = cls._deduplicate_graph_items(entities, "entity_key")
        relationships = cls._deduplicate_graph_items(relationships, "relation_key")
        entities = cls._filter_graph_items_by_chunk_paths(
            entities,
            chunks,
            "entity_key",
            "entity",
        )
        relationships = cls._filter_graph_items_by_chunk_paths(
            relationships,
            chunks,
            "relation_key",
            "relation",
        )
        if not cls._preserves_evidence_graph_context(preparation):
            entities = []
            relationships = []
        return KnowledgeRetrievalResult(
            chunks=chunks,
            entities=entities,
            relationships=relationships,
        )

    @classmethod
    async def _retrieve_target(
        cls,
        runtime: ProcessRuntime,
        client: Any,
        store: AsyncElasticSearchRetrieval,
        request: KnowledgeRetrievalRequest,
        target: RetrievalTarget,
        document_ids: list[str] | None,
        *,
        graph_target: GraphTargetSnapshot | None,
        use_request_reranker: bool,
        request_reranker: ModelRuntimeSnapshot | None,
        timings: RetrievalTimings | None = None,
        log_id: str | None = None,
    ) -> KnowledgeRetrievalResult:
        started_at = time.perf_counter()
        params = target.params
        target_type = params.retrieve_type
        if target_type is RetrieveType.Graph:
            if graph_target is None:
                cls._log_target_done(
                    target,
                    0,
                    0,
                    0,
                    0,
                    started_at,
                    timings=timings,
                )
                return KnowledgeRetrievalResult()
            result = await cls._retrieve_evidence_graph_target(
                runtime,
                client,
                request,
                target,
                graph_target,
                document_ids,
                timings=timings,
                log_id=log_id,
            )
            cls._log_target_done(
                target,
                0,
                0,
                len(result.chunks),
                len(result.chunks),
                started_at,
                timings=timings,
            )
            return result

        full_text_options = cls._search_options(
            request,
            target,
            document_ids,
            params.top_k if target_type is RetrieveType.PARTICIPLE else params.top_n,
            None if target_type is RetrieveType.PARTICIPLE else params.similarity_threshold,
        )
        if target_type is RetrieveType.PARTICIPLE:
            chunks = await store.search_by_full_text(request.query, full_text_options)
            cls._log_target_done(
                target,
                0,
                len(chunks),
                len(chunks),
                len(chunks),
                started_at,
                timings=timings,
            )
            return KnowledgeRetrievalResult(chunks=chunks)

        if target.embedding.resolved is None:
            raise KnowledgeError.from_code(
                "KB_MODEL_UNAVAILABLE",
                "Embedding model is unavailable",
            )
        embedding = RedBearEmbeddings(
            target.embedding.resolved,
            client_pool=runtime.model_runtime.pool,
        )
        vector_options = cls._search_options(
            request,
            target,
            document_ids,
            params.top_k if target_type is RetrieveType.SEMANTIC else params.top_n,
            params.vector_similarity_weight,
        )
        if target_type is RetrieveType.SEMANTIC:
            chunks = await store.search_by_vector(embedding, request.query, vector_options)
            cls._log_target_done(
                target,
                len(chunks),
                0,
                len(chunks),
                len(chunks),
                started_at,
                timings=timings,
            )
            return KnowledgeRetrievalResult(chunks=chunks)

        vector_task = asyncio.create_task(
            store.search_by_vector(embedding, request.query, vector_options)
        )
        text_task = asyncio.create_task(
            store.search_by_full_text(request.query, full_text_options)
        )
        graph_task = (
            asyncio.create_task(
                cls._retrieve_evidence_graph_channel(
                    runtime,
                    client,
                    request,
                    target,
                    graph_target,
                    document_ids,
                    timings=timings,
                    log_id=log_id,
                )
            )
            if (
                params.enable_graph_retrieval
                and graph_target is not None
                and graph_target.pipeline is GraphPipeline.EVIDENCE
            )
            else None
        )
        active_tasks = [vector_task, text_task]
        if graph_task is not None:
            active_tasks.append(graph_task)
        try:
            gathered = await asyncio.gather(*active_tasks)
        except BaseException:
            for task in active_tasks:
                task.cancel()
            await asyncio.gather(*active_tasks, return_exceptions=True)
            raise

        vector_chunks = gathered[0]
        text_chunks = gathered[1]
        graph_result = gathered[2] if graph_task is not None else KnowledgeRetrievalResult()
        candidates = cls._deduplicate_chunks(
            [*vector_chunks, *text_chunks, *graph_result.chunks]
        )
        reranker = request_reranker if use_request_reranker else target.reranker
        local_rerank_started_at = time.perf_counter()
        try:
            if candidates and reranker is not None:
                ranked = await cls._rerank_with_shared_model(
                    runtime,
                    reranker,
                    request.query,
                    candidates,
                    params.top_k,
                )
            elif candidates and use_request_reranker:
                ranked = cls._apply_rerank_fallback(candidates, params.top_k)
            else:
                ranked = candidates[: params.top_k]
        finally:
            cls._record_timing(timings, "local_rerank_ms", local_rerank_started_at)
        chunks = [
            chunk
            for chunk in ranked
            if float((chunk.metadata or {}).get("score") or 0)
            > params.rerank_score_threshold
        ]
        cls._log_target_done(
            target,
            len(vector_chunks),
            len(text_chunks),
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
        runtime: ProcessRuntime,
        client: Any,
        request: KnowledgeRetrievalRequest,
        target: RetrievalTarget,
        graph_target: GraphTargetSnapshot,
        document_ids: list[str] | None,
        *,
        timings: RetrievalTimings | None = None,
        log_id: str | None = None,
    ) -> KnowledgeRetrievalResult:
        return await cls._retrieve_evidence_graph_channel(
            runtime,
            client,
            request,
            target,
            graph_target,
            document_ids,
            timings=timings,
            log_id=log_id,
        )

    @classmethod
    async def _retrieve_evidence_graph_channel(
        cls,
        runtime: ProcessRuntime,
        client: Any,
        request: KnowledgeRetrievalRequest,
        target: RetrievalTarget,
        graph_target: GraphTargetSnapshot,
        document_ids: list[str] | None,
        *,
        timings: RetrievalTimings | None = None,
        log_id: str | None = None,
    ) -> KnowledgeRetrievalResult:
        if graph_target.knowledge_id != target.knowledge_id:
            raise ValueError("graph target does not match retrieval target")

        started_at = time.perf_counter()
        try:
            chunks, entities, relationships = await GraphRetrievalBridge.retrieve(
                runtime,
                client,
                GraphRetrievalSnapshot(
                    query=request.query,
                    pipeline=graph_target.pipeline,
                    targets=(graph_target,),
                    timings=timings,
                ),
                top_k=target.params.top_k,
                allowed_document_ids=(
                    tuple(document_ids) if document_ids is not None else None
                ),
                file_names=tuple(request.file_names_filter),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            stage = "timeout" if isinstance(exc, TimeoutError) else "pipeline"
            logger.warning(
                "[Retrieval] graph_target_failed id=%s kb_id=%s stage=%s "
                "error_type=%s elapsed_ms=%d",
                log_id or "unknown",
                cls._compact_id(target.knowledge_id),
                stage,
                type(exc).__name__,
                cls._elapsed_ms(started_at),
            )
            return KnowledgeRetrievalResult()
        finally:
            cls._record_timing(timings, "graph_ms", started_at)
        return KnowledgeRetrievalResult(
            chunks=chunks,
            entities=entities,
            relationships=relationships,
        )

    @classmethod
    async def _finalize_retrieval_chunks(
        cls,
        runtime: ProcessRuntime,
        request: KnowledgeRetrievalRequest,
        preparation: RetrievalPreparation,
        chunks: list[DocumentChunk],
        *,
        timings: RetrievalTimings | None = None,
        log_id: str | None = None,
    ) -> list[DocumentChunk]:
        candidates_count = len(chunks)
        unique_chunks = cls._deduplicate_chunks(chunks)
        if not unique_chunks:
            cls._log_finalize(
                log_id,
                candidates_count,
                0,
                False,
                None,
                0,
                timings,
            )
            return []

        targets = preparation.targets
        single_hybrid_uses_request_rerank = (
            request.rerank_id is not None
            and len(targets) == 1
            and targets[0].params.retrieve_type is RetrieveType.HYBRID
        )
        single_evidence_graph_target = (
            preparation.graph is not None
            and preparation.graph.pipeline is GraphPipeline.EVIDENCE
            and len(targets) == 1
            and targets[0].params.retrieve_type is RetrieveType.Graph
        )
        needs_global_rerank = not single_evidence_graph_target and (
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
                if reranker is not None:
                    ranked = await cls._rerank_with_shared_model(
                        runtime,
                        reranker,
                        request.query,
                        unique_chunks,
                        request.top_k,
                    )
                else:
                    ranked = cls._apply_rerank_fallback(unique_chunks, request.top_k)
                threshold = cls._resolve_rerank_score_threshold(request)
                filtered = [
                    chunk
                    for chunk in ranked
                    if float((chunk.metadata or {}).get("score") or 0) > threshold
                ]
            finally:
                cls._record_timing(
                    timings,
                    "global_rerank_ms",
                    global_rerank_started_at,
                )
        else:
            threshold = None
            filtered = sorted(
                unique_chunks,
                key=lambda chunk: float((chunk.metadata or {}).get("score") or 0),
                reverse=True,
            )
        result = filtered[: request.top_k]
        cls._log_finalize(
            log_id,
            candidates_count,
            len(unique_chunks),
            needs_global_rerank,
            threshold,
            len(result),
            timings,
        )
        return result

    @staticmethod
    def _search_options(
        request: KnowledgeRetrievalRequest,
        target: RetrievalTarget,
        document_ids: list[str] | None,
        top_k: int,
        threshold: float | None,
    ) -> RetrievalSearchOptions:
        return RetrievalSearchOptions(
            indices=target.index_name,
            top_k=top_k,
            score_threshold=threshold,
            file_names_filter=tuple(request.file_names_filter),
            document_ids_include=(
                tuple(document_ids) if document_ids is not None else None
            ),
            knn_num_candidates=None,
        )

    @classmethod
    async def _rerank_with_shared_model(
        cls,
        runtime: ProcessRuntime,
        snapshot: ModelRuntimeSnapshot,
        query: str,
        chunks: Sequence[DocumentChunk],
        top_k: int,
    ) -> list[DocumentChunk]:
        if top_k <= 0 or not chunks:
            return []
        if snapshot.resolved is None:
            return cls._apply_rerank_fallback(chunks, top_k)
        documents = [
            LangChainDocument(
                page_content=chunk_retrieval_content(chunk),
                metadata={**(chunk.metadata or {}), _SOURCE_INDEX: index},
            )
            for index, chunk in enumerate(chunks)
        ]
        try:
            reranker = RedBearRerank(snapshot.resolved)
            reranked = list(await reranker.acompress_documents(documents, query))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "Rerank failed; using retrieval order provider=%s error_type=%s",
                snapshot.provider,
                type(exc).__name__,
            )
            return cls._apply_rerank_fallback(chunks, top_k)

        reranked.sort(
            key=lambda item: float(item.metadata.get("relevance_score") or 0),
            reverse=True,
        )
        result: list[DocumentChunk] = []
        for item in reranked[:top_k]:
            index = item.metadata.get(_SOURCE_INDEX)
            if (
                not isinstance(index, int)
                or isinstance(index, bool)
                or not 0 <= index < len(chunks)
            ):
                continue
            chunk = chunks[index]
            chunk.metadata["score"] = float(item.metadata.get("relevance_score") or 0)
            result.append(chunk)
        return result

    @staticmethod
    async def _build_metadata_filter_groups(
        runtime: ProcessRuntime,
        request: KnowledgeRetrievalRequest,
        preparation: RetrievalPreparation,
    ) -> list[FilterGroup]:
        if request.metadata_filter_mode is MetadataFilterMode.DISABLED:
            return []
        common_fields = set(preparation.common_metadata_defs)
        groups = [
            FilterGroup(
                [
                    FilterCondition(
                        condition.field,
                        condition.operator,
                        condition.value,
                    )
                    for condition in group.conditions
                    if condition.field in common_fields
                ],
                group.logic,
            )
            for group in request.metadata_filters
            if any(condition.field in common_fields for condition in group.conditions)
        ]
        if (
            groups
            or bool(request.metadata_filters)
            or request.metadata_filter_mode is MetadataFilterMode.MANUAL
            or request.metadata_filters_resolved
        ):
            return groups
        if preparation.metadata_llm is None or preparation.metadata_llm.resolved is None:
            return []
        llm = RedBearLLM(
            preparation.metadata_llm.resolved,
            client_pool=runtime.model_runtime.pool,
        )
        return await generate_filter_groups(
            request.query,
            dict(preparation.common_metadata_defs),
            llm,
        )

    @staticmethod
    def _resolve_rerank_score_threshold(request: KnowledgeRetrievalRequest) -> float:
        if request.rerank_score_threshold is not None:
            return request.rerank_score_threshold
        if request.vector_similarity_weight is not None:
            return request.vector_similarity_weight
        return 0.1

    @staticmethod
    def _deduplicate_chunks(chunks: Sequence[DocumentChunk]) -> list[DocumentChunk]:
        seen: set[tuple[Any, ...]] = set()
        result: list[DocumentChunk] = []
        for chunk in chunks:
            metadata = chunk.metadata or {}
            if metadata.get("doc_id"):
                key = ("doc_id", metadata["doc_id"])
            elif metadata.get("document_id") is not None and metadata.get("sort_id") is not None:
                key = ("document_sort", metadata["document_id"], metadata["sort_id"])
            else:
                key = ("content", chunk.page_content)
            if key not in seen:
                seen.add(key)
                result.append(chunk)
        return result

    @staticmethod
    def _apply_rerank_fallback(
        chunks: Sequence[DocumentChunk],
        top_k: int,
    ) -> list[DocumentChunk]:
        result = list(chunks[:top_k])
        for chunk in result:
            chunk.metadata.setdefault("score", 0.5)
        return result

    @staticmethod
    def _preserves_evidence_graph_context(
        preparation: RetrievalPreparation,
    ) -> bool:
        return (
            len(preparation.targets) == 1
            and preparation.targets[0].params.retrieve_type is RetrieveType.Graph
            and preparation.graph is not None
            and preparation.graph.pipeline is GraphPipeline.EVIDENCE
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
                return result

    @staticmethod
    def _deduplicate_graph_items(
        items: Sequence[Any],
        key_field: str,
    ) -> list[Any]:
        seen: set[str] = set()
        result: list[Any] = []
        for item in items:
            key = KnowledgeRetrievalService._graph_item_key(item, key_field)
            if key and key not in seen:
                seen.add(key)
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
    def _graph_entities_to_chunk(cls, entities: Sequence[Any]) -> DocumentChunk:
        return DocumentChunk(
            page_content="\n".join(
                ["Entities:"]
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
                "source_chunk_ids": cls._graph_items_unique_list(
                    entities,
                    "source_chunk_ids",
                ),
            },
        )

    @classmethod
    def _graph_relationships_to_chunk(
        cls,
        relationships: Sequence[Any],
    ) -> DocumentChunk:
        return DocumentChunk(
            page_content="\n".join(
                ["Relationships:"]
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
                "source_chunk_ids": cls._graph_items_unique_list(
                    relationships,
                    "source_chunk_ids",
                ),
            },
        )

    @classmethod
    def _format_graph_entity_line(cls, entity: Any, index: int) -> str:
        entity_key = cls._graph_item_text(entity, "entity_key")
        entity_name = cls._graph_item_text(entity, "entity_name") or entity_key
        description = cls._graph_item_text(entity, "description")
        parts = [entity_name or "unknown"]
        if description:
            parts.append(description)
        return f"{index}. " + " - ".join(parts)

    @classmethod
    def _format_graph_relationship_line(cls, relationship: Any, index: int) -> str:
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
        connector = "->" if cls._graph_item_value(relationship, "directed") is not False else "--"
        endpoints = (
            f"{from_name} {connector} {to_name}"
            if from_name and to_name
            else from_name or to_name
        )
        parts = [endpoints or relation_key or "unknown"]
        label = (
            cls._graph_item_text(relationship, "predicate")
            or cls._graph_item_text(relationship, "label")
        )
        description = cls._graph_item_text(relationship, "description")
        if label:
            parts.append(label)
        if description:
            parts.append(description)
        return f"{index}. " + " - ".join(parts)

    @classmethod
    def _graph_items_unique_list(cls, items: Sequence[Any], key: str) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for item in items:
            for value in cls._graph_item_list(item, key):
                if value not in seen:
                    seen.add(value)
                    result.append(value)
        return result

    @staticmethod
    def _graph_item_value(item: Any, key: str) -> Any:
        return item.get(key) if isinstance(item, dict) else getattr(item, key, None)

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
            return [str(nested).strip() for nested in value if str(nested).strip()]
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
            return []
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
            paths = (chunk.metadata or {}).get("match_paths") or []
            if not isinstance(paths, list):
                continue
            for path in paths:
                if not isinstance(path, dict) or path.get("evidence_type") != projection_type:
                    continue
                key = str(path.get("evidence_key") or "")
                if key:
                    keys.add(key)
        return keys

    @staticmethod
    def _graph_item_key(item: Any, key_field: str) -> str:
        value = item.get(key_field) if isinstance(item, dict) else getattr(item, key_field, "")
        return str(value or "")

    @staticmethod
    def _include_document_ids(
        chunks: Sequence[DocumentChunk],
        document_ids: list[str] | None,
    ) -> list[DocumentChunk]:
        if document_ids is None:
            return list(chunks)
        include = set(document_ids)
        return [
            chunk
            for chunk in chunks
            if (chunk.metadata or {}).get("document_id") in include
        ]

    @staticmethod
    def _new_retrieval_log_id() -> str:
        return uuid.uuid4().hex[:8]

    @classmethod
    def _build_retrieval_start_log_fields(
        cls,
        log_id: str,
        request: KnowledgeRetrievalRequest,
        principal: Principal,
        timings: RetrievalTimings,
    ) -> dict[str, Any]:
        return {
            "id": log_id,
            "actor": cls._compact_id(principal.actor_id),
            "kb_count": len(request.kb_ids),
            "ex_id_count": len(request.ex_ids),
            "knowledge_base_count": len(request.knowledge_bases),
            "query_len": len(request.query),
            "type": request.retrieve_type,
            "top_k": request.top_k,
            "top_n": request.top_n,
            "enable_graph_retrieval": request.enable_graph_retrieval,
            "metadata_mode": request.metadata_filter_mode,
            "async_mode": "native",
        } | timings.as_log_fields()

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
        return KnowledgeRetrievalResult()

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

    @classmethod
    def _log_finalize(
        cls,
        log_id: str | None,
        candidates_count: int,
        unique_count: int,
        global_rerank: bool,
        threshold: float | None,
        result_count: int,
        timings: RetrievalTimings | None,
    ) -> None:
        logger.info(
            "[Retrieval] finalize %s",
            cls._format_log_fields(
                {
                    "id": log_id or "unknown",
                    "candidates": candidates_count,
                    "deduped": unique_count,
                    "global_rerank": global_rerank,
                    "threshold": threshold if threshold is not None else "none",
                    "result_count": result_count,
                    "async_mode": "native",
                }
                | cls._timing_log_fields(timings)
            ),
        )

    @classmethod
    def _log_metadata_filter(
        cls,
        log_id: str,
        request: KnowledgeRetrievalRequest,
        preparation: RetrievalPreparation,
        filter_groups: list[FilterGroup],
        document_ids: list[str] | None,
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
                        len(document_ids) if document_ids is not None else "none"
                    ),
                    "async_mode": "native",
                }
            ),
        )

    @staticmethod
    def _timing_log_fields(timings: RetrievalTimings | None) -> dict[str, int]:
        return (
            timings.as_log_fields()
            if timings is not None
            else RetrievalTimings().as_log_fields()
        )

    @classmethod
    def _format_log_fields(cls, fields: dict[str, Any]) -> str:
        return " ".join(
            f"{key}={cls._format_log_value(value)}" for key, value in fields.items()
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
    def _compact_ids(cls, values: Sequence[Any], limit: int = 10) -> list[str]:
        compacted = [cls._compact_id(value) for value in values[:limit]]
        if len(values) > limit:
            compacted.append(f"+{len(values) - limit}")
        return compacted

    @staticmethod
    def _compact_id(value: Any) -> str:
        text = str(value)
        return text[:8] if len(text) > 8 else text

    @staticmethod
    def _elapsed_ms(started_at: float) -> int:
        return max(0, int((time.perf_counter() - started_at) * 1000))

    @classmethod
    def _record_timing(
        cls,
        timings: RetrievalTimings | None,
        field: str,
        started_at: float,
    ) -> None:
        del cls
        _record_elapsed(timings, field, started_at)


__all__ = ["KnowledgeRetrievalService"]
