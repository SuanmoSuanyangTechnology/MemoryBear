import asyncio
import logging
import os
from typing import Any, Protocol
from urllib.parse import urlparse

from elasticsearch import AsyncElasticsearch

from app.core.config import settings
from app.core.rag.models.chunk import DocumentChunk
from app.core.rag.retrieval.elasticsearch_queries import (
    VECTOR_SEARCH_MODE_KNN,
    build_full_text_query,
    build_knn_query,
    build_parent_lookup_query,
    build_vector_filter_clauses,
    build_vector_script_query,
    full_text_hits_to_chunks,
    merge_parent_chunks,
    normalize_vector,
    raise_on_shard_failures,
    resolve_vector_search_mode,
    vector_hits_to_chunks,
)
from app.core.rag.retrieval.models import ModelRuntimeSnapshot, RetrievalSearchOptions


logger = logging.getLogger(__name__)


class AsyncEmbeddingClient(Protocol):
    async def embed_query(
        self,
        embedding: ModelRuntimeSnapshot,
        query: str,
    ) -> list[float]: ...


def build_async_elasticsearch_client_config() -> dict[str, Any]:
    parsed = urlparse(settings.ELASTICSEARCH_HOST)
    scheme = parsed.scheme or "https"
    hostname = parsed.hostname or settings.ELASTICSEARCH_HOST
    config: dict[str, Any] = {
        "hosts": [f"{scheme}://{hostname}:{settings.ELASTICSEARCH_PORT}"],
        "basic_auth": (settings.ELASTICSEARCH_USERNAME, settings.ELASTICSEARCH_PASSWORD),
        "request_timeout": settings.ELASTICSEARCH_REQUEST_TIMEOUT,
        "retry_on_timeout": settings.ELASTICSEARCH_RETRY_ON_TIMEOUT,
        "max_retries": settings.ELASTICSEARCH_MAX_RETRIES,
        "connections_per_node": int(os.getenv("ELASTICSEARCH_CONNECTIONS_PER_NODE", "10")),
    }
    if scheme == "https":
        config["verify_certs"] = settings.ELASTICSEARCH_VERIFY_CERTS
        if settings.ELASTICSEARCH_CA_CERTS:
            config["ca_certs"] = settings.ELASTICSEARCH_CA_CERTS
    return config


class AsyncElasticsearchClientProvider:
    _client: AsyncElasticsearch | None = None
    _lock = asyncio.Lock()

    @classmethod
    async def get_shared_client(cls) -> AsyncElasticsearch:
        async with cls._lock:
            if cls._client is None:
                cls._client = AsyncElasticsearch(**build_async_elasticsearch_client_config())
            return cls._client

    @classmethod
    async def aclose(cls) -> None:
        if cls._client is not None:
            await cls._client.close()
            cls._client = None


class AsyncElasticSearchRetrieval:
    def __init__(self, client: AsyncElasticsearch, models: AsyncEmbeddingClient) -> None:
        self._client = client
        self._models = models

    async def search_by_vector(
        self,
        embedding: ModelRuntimeSnapshot,
        query: str,
        options: RetrievalSearchOptions,
    ) -> list[DocumentChunk]:
        query_vector = normalize_vector(await self._models.embed_query(embedding, query))
        filters = build_vector_filter_clauses(
            options.file_names_filter,
            options.document_ids_include,
        )

        if resolve_vector_search_mode() == VECTOR_SEARCH_MODE_KNN:
            try:
                result = await self._client.search(
                    index=options.indices,
                    size=options.top_k,
                    knn=build_knn_query(
                        query_vector,
                        options.top_k,
                        filters,
                        options.knn_num_candidates,
                    ),
                )
                return await self._resolve_vector_result(
                    result,
                    options,
                    normalize_script_score=False,
                )
            except Exception as exc:
                if "Elasticsearch shard failures" in str(exc):
                    raise
                logger.warning("[ES search_by_vector] KNN search failed; using script score: %s", exc)

        result = await self._client.search(
            index=options.indices,
            from_=0,
            size=options.top_k,
            query=build_vector_script_query(query_vector, filters),
        )
        return await self._resolve_vector_result(
            result,
            options,
            normalize_script_score=True,
        )

    async def search_by_full_text(
        self,
        query: str,
        options: RetrievalSearchOptions,
    ) -> list[DocumentChunk]:
        result = await self._client.search(
            index=options.indices,
            from_=0,
            size=options.top_k,
            query=build_full_text_query(
                query,
                options.file_names_filter,
                options.document_ids_include,
            ),
        )
        raise_on_shard_failures(result, "full text search")
        docs = full_text_hits_to_chunks(result, options.score_threshold)
        return await self.resolve_parent_chunks(docs, index=options.indices)

    async def resolve_parent_chunks(
        self,
        chunks: list[DocumentChunk],
        index: str,
    ) -> list[DocumentChunk]:
        parent_ids = list(
            {
                doc.metadata.get("parent_id", "")
                for doc in chunks
                if (doc.metadata or {}).get("chunk_type") == "child"
                and doc.metadata.get("parent_id")
            }
        )
        if not parent_ids:
            return chunks

        try:
            result = await self._client.search(
                index=index,
                size=len(parent_ids),
                query=build_parent_lookup_query(parent_ids),
            )
        except Exception as exc:
            logger.warning("Failed to resolve parent chunks: %s", exc)
            return chunks
        return merge_parent_chunks(chunks, result.get("hits", {}).get("hits", []))

    async def _resolve_vector_result(
        self,
        result: dict[str, Any],
        options: RetrievalSearchOptions,
        *,
        normalize_script_score: bool,
    ) -> list[DocumentChunk]:
        raise_on_shard_failures(result, "vector search")
        docs = vector_hits_to_chunks(
            result,
            options.score_threshold,
            normalize_script_score=normalize_script_score,
        )
        return await self.resolve_parent_chunks(docs, index=options.indices)
