"""Native asynchronous knowledge retrieval copied from the legacy API."""

from __future__ import annotations

import asyncio
import logging
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
from ..rag.metadata.auto_filter import generate_filter_groups
from ..rag.metadata.filter_engine import FilterCondition, FilterGroup
from ..rag.models.chunk import DocumentChunk, chunk_retrieval_content
from ..rag.retrieval.async_elasticsearch import AsyncElasticSearchRetrieval
from ..rag.retrieval.graph_bridge import GraphRetrievalBridge
from ..rag.retrieval.models import (
    ModelRuntimeSnapshot,
    RetrievalPreparation,
    RetrievalSearchOptions,
    RetrievalTarget,
)
from ..runtime import ProcessRuntime
from .knowledge_retrieval_preparation import KnowledgeRetrievalPreparation

logger = logging.getLogger(__name__)
_SOURCE_INDEX = "_retrieval_source_index"


class KnowledgeRetrievalService:
    @classmethod
    async def retrieve_async(
        cls,
        runtime: ProcessRuntime,
        request: KnowledgeRetrievalRequest,
        principal: Principal,
    ) -> KnowledgeRetrievalResult:
        async with runtime.database.async_session() as db:
            preparation = await KnowledgeRetrievalPreparation.prepare_with_db(
                db,
                request,
                principal,
            )
            if not preparation.targets:
                return KnowledgeRetrievalResult()
        filter_groups = await cls._build_metadata_filter_groups(
            runtime,
            request,
            preparation,
        )
        async with runtime.database.async_session() as db:
            document_ids = await KnowledgeRetrievalPreparation.resolve_metadata_document_ids(
                db,
                preparation,
                filter_groups,
            )
        if document_ids == []:
            return KnowledgeRetrievalResult()
        client = await runtime.elasticsearch.client()
        store = AsyncElasticSearchRetrieval(client)
        tasks = [
            asyncio.create_task(cls._retrieve_target(runtime, store, request, target, document_ids))
            for target in preparation.targets
        ]
        try:
            groups = await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        chunks = [chunk for group in groups for chunk in group]
        entities: list[dict[str, Any]] = []
        relationships: list[dict[str, Any]] = []
        if preparation.graph is not None:
            graph_chunks, entities, relationships = await GraphRetrievalBridge.retrieve(
                client,
                preparation.graph,
                top_k=request.top_k,
            )
            chunks = graph_chunks + chunks
        chunks = cls._deduplicate_chunks(chunks)
        if len(preparation.targets) > 1 or request.rerank_id is not None:
            reranker = preparation.request_reranker or preparation.targets[0].reranker
            if reranker is not None:
                chunks = await cls._rerank_with_shared_model(
                    runtime,
                    reranker,
                    request.query,
                    chunks,
                    request.top_k,
                )
        chunks = sorted(
            chunks,
            key=lambda chunk: float((chunk.metadata or {}).get("score") or 0),
            reverse=True,
        )[: request.top_k]
        if entities:
            chunks.insert(0, cls._graph_entities_to_chunk(entities))
        if relationships:
            chunks.insert(1 if entities else 0, cls._graph_relationships_to_chunk(relationships))
        return KnowledgeRetrievalResult(chunks=chunks)

    @classmethod
    async def _retrieve_target(
        cls,
        runtime: ProcessRuntime,
        store: AsyncElasticSearchRetrieval,
        request: KnowledgeRetrievalRequest,
        target: RetrievalTarget,
        document_ids: list[str] | None,
    ) -> list[DocumentChunk]:
        params = target.params
        full_text_options = cls._search_options(
            request,
            target,
            document_ids,
            params.top_k if params.retrieve_type is RetrieveType.PARTICIPLE else params.top_n,
            None
            if params.retrieve_type is RetrieveType.PARTICIPLE
            else params.similarity_threshold,
        )
        if params.retrieve_type is RetrieveType.PARTICIPLE:
            return await store.search_by_full_text(request.query, full_text_options)
        if target.embedding.resolved is None:
            raise KnowledgeError.from_code("KB_MODEL_UNAVAILABLE", "Embedding model is unavailable")
        embedding = RedBearEmbeddings(
            target.embedding.resolved,
            client_pool=runtime.model_runtime.pool,
        )
        vector_options = cls._search_options(
            request,
            target,
            document_ids,
            params.top_k if params.retrieve_type is RetrieveType.SEMANTIC else params.top_n,
            params.vector_similarity_weight,
        )
        if params.retrieve_type is RetrieveType.SEMANTIC:
            return await store.search_by_vector(embedding, request.query, vector_options)
        if params.retrieve_type is RetrieveType.Graph:
            return []
        vector_task = asyncio.create_task(
            store.search_by_vector(embedding, request.query, vector_options)
        )
        text_task = asyncio.create_task(store.search_by_full_text(request.query, full_text_options))
        vector_chunks, text_chunks = await asyncio.gather(vector_task, text_task)
        candidates = cls._deduplicate_chunks([*vector_chunks, *text_chunks])
        if target.reranker is None:
            ranked = cls._apply_rerank_fallback(candidates, params.top_k)
        else:
            ranked = await cls._rerank_with_shared_model(
                runtime,
                target.reranker,
                request.query,
                candidates,
                params.top_k,
            )
        return [
            chunk
            for chunk in ranked
            if float((chunk.metadata or {}).get("score") or 0) > params.rerank_score_threshold
        ]

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
            document_ids_include=tuple(document_ids) if document_ids is not None else None,
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
        if not chunks:
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
            del runtime
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
        result = []
        for item in reranked:
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
        return sorted(result, key=lambda chunk: chunk.metadata["score"], reverse=True)[:top_k]

    @staticmethod
    async def _build_metadata_filter_groups(
        runtime: ProcessRuntime,
        request: KnowledgeRetrievalRequest,
        preparation: RetrievalPreparation,
    ) -> list[FilterGroup]:
        if request.metadata_filter_mode is MetadataFilterMode.DISABLED:
            return []
        common_fields = set(preparation.common_metadata_defs)
        groups = []
        for group in request.metadata_filters:
            conditions = [
                FilterCondition(condition.field, condition.operator, condition.value)
                for condition in group.conditions
                if condition.field in common_fields
            ]
            if conditions:
                groups.append(FilterGroup(conditions, group.logic))
        if groups or request.metadata_filter_mode is MetadataFilterMode.MANUAL:
            return groups
        if preparation.metadata_llm is None or preparation.metadata_llm.resolved is None:
            return []
        llm = RedBearLLM(
            preparation.metadata_llm.resolved,
            client_pool=runtime.model_runtime.pool,
        )
        return await generate_filter_groups(
            request.query,
            preparation.common_metadata_defs,
            llm,
        )

    @staticmethod
    def _deduplicate_chunks(chunks: Sequence[DocumentChunk]) -> list[DocumentChunk]:
        seen = set()
        result = []
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
    def _graph_entities_to_chunk(items: list[dict[str, Any]]) -> DocumentChunk:
        return DocumentChunk(
            page_content="Entities:\n"
            + "\n".join(
                f"{index}. {item.get('entity_name') or item['entity_key']} - "
                f"{item.get('description', '')}"
                for index, item in enumerate(items, 1)
            ),
            metadata={
                "doc_id": "graph_entities",
                "chunk_type": "graph_entities",
                "retrieval_source": "graph",
                "score": 1.0,
            },
        )

    @staticmethod
    def _graph_relationships_to_chunk(items: list[dict[str, Any]]) -> DocumentChunk:
        return DocumentChunk(
            page_content="Relationships:\n"
            + "\n".join(
                f"{index}. {item.get('from_entity_key')} -> {item.get('to_entity_key')} - "
                f"{item.get('predicate') or item.get('description', '')}"
                for index, item in enumerate(items, 1)
            ),
            metadata={
                "doc_id": "graph_relationships",
                "chunk_type": "graph_relationships",
                "retrieval_source": "graph",
                "score": 1.0,
            },
        )


__all__ = ["KnowledgeRetrievalService"]
