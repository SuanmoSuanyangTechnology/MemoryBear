"""Native asynchronous knowledge retrieval with legacy response semantics."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Sequence
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


class KnowledgeRetrievalService:
    @classmethod
    async def retrieve_async(
        cls,
        runtime: ProcessRuntime,
        request: KnowledgeRetrievalRequest,
        principal: Principal,
    ) -> KnowledgeRetrievalResult:
        timings = RetrievalTimings()
        snapshot_started_at = time.perf_counter()
        async with runtime.database.async_session() as db:
            preparation = await KnowledgeRetrievalPreparation.prepare_with_db(
                db,
                request,
                principal,
            )
        cls._record_timing(timings, "db_snapshot_ms", snapshot_started_at)
        if not preparation.targets:
            return KnowledgeRetrievalResult()

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
        if document_ids == []:
            return KnowledgeRetrievalResult()

        client = await runtime.elasticsearch.client()
        store = AsyncElasticSearchRetrieval(client)
        retrieval_result = await cls._retrieve_prepared(
            runtime,
            client,
            store,
            request,
            preparation,
            document_ids,
            timings=timings,
        )
        chunks = cls._include_document_ids(retrieval_result.chunks, document_ids)
        graph_context_chunks = cls._build_graph_context_chunks(
            retrieval_result.entities,
            retrieval_result.relationships,
        )
        return KnowledgeRetrievalResult(chunks=graph_context_chunks + chunks)

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
    ) -> KnowledgeRetrievalResult:
        params = target.params
        target_type = params.retrieve_type
        if target_type is RetrieveType.Graph:
            if graph_target is None:
                return KnowledgeRetrievalResult()
            return await cls._retrieve_evidence_graph_target(
                runtime,
                client,
                request,
                target,
                graph_target,
                document_ids,
                timings=timings,
            )

        full_text_options = cls._search_options(
            request,
            target,
            document_ids,
            params.top_k if target_type is RetrieveType.PARTICIPLE else params.top_n,
            None if target_type is RetrieveType.PARTICIPLE else params.similarity_threshold,
        )
        if target_type is RetrieveType.PARTICIPLE:
            chunks = await cls._timed_awaitable(
                store.search_by_full_text(request.query, full_text_options),
                timings,
                "es_fulltext_ms",
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
            chunks = await cls._timed_awaitable(
                store.search_by_vector(embedding, request.query, vector_options),
                timings,
                "es_vector_ms",
            )
            return KnowledgeRetrievalResult(chunks=chunks)

        vector_task = asyncio.create_task(
            cls._timed_awaitable(
                store.search_by_vector(embedding, request.query, vector_options),
                timings,
                "es_vector_ms",
            )
        )
        text_task = asyncio.create_task(
            cls._timed_awaitable(
                store.search_by_full_text(request.query, full_text_options),
                timings,
                "es_fulltext_ms",
            )
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
    ) -> KnowledgeRetrievalResult:
        return await cls._retrieve_evidence_graph_channel(
            runtime,
            client,
            request,
            target,
            graph_target,
            document_ids,
            timings=timings,
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
                "[Retrieval] graph_target_failed kb_id=%s stage=%s error_type=%s elapsed_ms=%d",
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
    ) -> list[DocumentChunk]:
        unique_chunks = cls._deduplicate_chunks(chunks)
        if not unique_chunks:
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
            filtered = sorted(
                unique_chunks,
                key=lambda chunk: float((chunk.metadata or {}).get("score") or 0),
                reverse=True,
            )
        return filtered[: request.top_k]

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
            or request.metadata_filters_prepared
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
    def _compact_id(value: Any) -> str:
        return str(value)[:8]

    @staticmethod
    def _elapsed_ms(started_at: float) -> int:
        return max(0, int((time.perf_counter() - started_at) * 1000))

    @classmethod
    async def _timed_awaitable(
        cls,
        awaitable: Any,
        timings: RetrievalTimings | None,
        field: str,
    ) -> Any:
        started_at = time.perf_counter()
        try:
            return await awaitable
        finally:
            cls._record_timing(timings, field, started_at)

    @classmethod
    def _record_timing(
        cls,
        timings: RetrievalTimings | None,
        field: str,
        started_at: float,
    ) -> None:
        if timings is not None:
            setattr(timings, field, getattr(timings, field) + cls._elapsed_ms(started_at))


__all__ = ["KnowledgeRetrievalService"]
