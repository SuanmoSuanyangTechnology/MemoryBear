"""Async Elasticsearch chunk CRUD copied from the legacy vector service."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from typing import Any, Protocol

from elasticsearch.helpers import async_bulk

from ..models.chunk import DocumentChunk, chunk_retrieval_content
from ..vdb.field import Field
from ..vdb.pit_search import iter_async_search_after_hits
from .elasticsearch_queries import (
    build_filter_clauses,
    build_full_text_query,
    build_parent_lookup_query,
    build_vector_script_query,
    full_text_hits_to_chunks,
    merge_parent_chunks,
    normalize_vector,
    vector_hits_to_chunks,
)
from .models import RetrievalSearchOptions

ES_DEFAULT_MAX_RESULT_WINDOW = 10000
ES_FULL_SCAN_BATCH_SIZE = 1000
EmbedFunction = Callable[[list[str]], Awaitable[list[list[float]]]]


class AsyncEmbeddingClient(Protocol):
    async def aembed_query(self, text: str) -> list[float]: ...


def collection_name_for_knowledge(knowledge_id: uuid.UUID | str) -> str:
    return f"Vector_index_{knowledge_id}_Node".lower()


class AsyncChunkStore:
    def __init__(
        self,
        client: Any,
        knowledge_id: uuid.UUID | str,
        *,
        embed: EmbedFunction | None = None,
    ):
        self.client = client
        self.index = collection_name_for_knowledge(knowledge_id)
        self.embed = embed

    @staticmethod
    def build_segment_query(
        document_id: str | None,
        query: str | None,
        chunk_types: list[str] | str | None,
        parent_ids: list[str] | str | None,
    ) -> dict[str, Any]:
        must: list[dict[str, Any]] = []
        if document_id:
            must.append({"term": {Field.DOCUMENT_ID.value: document_id}})
        if query:
            must.append(
                {
                    "multi_match": {
                        "query": query,
                        "fields": [Field.CONTENT_KEY.value, Field.VISION_TEXT.value],
                        "analyzer": "ik_max_word",
                    }
                }
            )
        if chunk_types:
            values = chunk_types if isinstance(chunk_types, list) else [chunk_types]
            must.append({"terms": {Field.CHUNK_TYPE.value: values}})
        if parent_ids:
            values = parent_ids if isinstance(parent_ids, list) else [parent_ids]
            must.append({"terms": {f"metadata.{Field.PARENT_ID.value}": values}})
        return {"bool": {"must": must}}

    @staticmethod
    def segment_sort(asc: bool) -> list[dict[str, Any]]:
        order = "asc" if asc else "desc"
        return [
            {
                Field.SORT_ID.value: {
                    "order": order,
                    "unmapped_type": "long",
                    "missing": "_last",
                }
            },
            {
                Field.DOC_ID.value: {
                    "order": order,
                    "unmapped_type": "keyword",
                    "missing": "_last",
                }
            },
        ]

    @staticmethod
    def hit_to_chunk(hit: Mapping[str, Any]) -> DocumentChunk:
        source = hit.get("_source") or {}
        metadata = dict(source.get(Field.METADATA_KEY.value) or {})
        chunk_type = source.get(Field.CHUNK_TYPE.value)
        page_content = source.get(Field.CONTENT_KEY.value) or ""
        if chunk_type:
            metadata["chunk_type"] = chunk_type
        if chunk_type == "qa":
            metadata["question"] = source.get(Field.QUESTION.value, "")
            metadata["answer"] = source.get(Field.ANSWER.value, "")
            page_content = f"question: {metadata['question']}\nanswer: {metadata['answer']}"
        metadata["score"] = hit.get("_score")
        return DocumentChunk(page_content=page_content, vector=None, metadata=metadata)

    async def search_by_segment(
        self,
        *,
        document_id: str | None = None,
        query: str | None = None,
        pagesize: int = 10,
        page: int = 1,
        asc: bool = True,
        chunk_types: list[str] | str | None = None,
        parent_ids: list[str] | str | None = None,
    ) -> tuple[int, list[DocumentChunk]]:
        if not await self.client.indices.exists(index=self.index):
            return 0, []
        offset = pagesize * (page - 1)
        segment_query = self.build_segment_query(
            document_id,
            query,
            chunk_types,
            parent_ids,
        )
        if offset + pagesize > ES_DEFAULT_MAX_RESULT_WINDOW:
            hits = [
                hit
                async for hit in self.iter_by_segment(
                    document_id=document_id,
                    query=query,
                    asc=asc,
                    chunk_types=chunk_types,
                    parent_ids=parent_ids,
                )
            ]
            return len(hits), [self.hit_to_chunk(hit) for hit in hits[offset : offset + pagesize]]
        response = await self.client.search(
            index=self.index,
            from_=offset,
            size=pagesize,
            query=segment_query,
            sort=self.segment_sort(asc),
            track_total_hits=True,
            allow_partial_search_results=False,
        )
        self._raise_on_failed_response(response, "segment search")
        hits = response.get("hits", {}).get("hits", [])
        total = int(response.get("hits", {}).get("total", {}).get("value", 0))
        return total, [self.hit_to_chunk(hit) for hit in hits]

    async def iter_by_segment(
        self,
        *,
        document_id: str | None = None,
        query: str | None = None,
        asc: bool = True,
        chunk_types: list[str] | str | None = None,
        parent_ids: list[str] | str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        if not await self.client.indices.exists(index=self.index):
            return
        async for hit in iter_async_search_after_hits(
            self.client,
            index=self.index,
            query=self.build_segment_query(
                document_id,
                query,
                chunk_types,
                parent_ids,
            ),
            sort=self.segment_sort(asc),
            batch_size=ES_FULL_SCAN_BATCH_SIZE,
        ):
            yield hit

    async def get_by_segment(self, doc_id: str) -> DocumentChunk | None:
        if not await self.client.indices.exists(index=self.index):
            return None
        response = await self.client.search(
            index=self.index,
            from_=0,
            size=1,
            query={"term": {Field.DOC_ID.value: doc_id}},
        )
        self._raise_on_failed_response(response, "segment get")
        hits = response.get("hits", {}).get("hits", [])
        if not hits:
            return None
        hit = hits[0]
        source = hit.get("_source") or {}
        metadata = dict(source.get(Field.METADATA_KEY.value) or {})
        metadata["score"] = hit.get("_score")
        return DocumentChunk(
            page_content=source.get(Field.CONTENT_KEY.value) or "",
            vector=source.get(Field.VECTOR.value),
            metadata=metadata,
        )

    async def add_chunks(self, chunks: list[DocumentChunk]) -> None:
        if not chunks:
            return
        embeddings = await self._embed_chunks(chunks)
        if not await self.client.indices.exists(index=self.index):
            await self._create_index(embeddings)
        actions = []
        for chunk, vector in zip(chunks, embeddings, strict=True):
            metadata = dict(chunk.metadata or {})
            source: dict[str, Any] = {
                Field.CONTENT_KEY.value: chunk.page_content,
                Field.METADATA_KEY.value: metadata,
                Field.VECTOR.value: vector,
            }
            for field, key in (
                (Field.CHUNK_TYPE, "chunk_type"),
                (Field.QUESTION, "question"),
                (Field.ANSWER, "answer"),
                (Field.SOURCE_CHUNK_ID, "source_chunk_id"),
                (Field.PARENT_ID, "parent_id"),
            ):
                if metadata.get(key):
                    source[field.value] = metadata[key]
            actions.append({"_index": self.index, "_source": source})
        await async_bulk(self.client, actions)

    async def update_chunk(self, chunk: DocumentChunk) -> int:
        metadata = chunk.metadata or {}
        chunk_type = metadata.get("chunk_type")
        vector = None
        if chunk_type != "source":
            vector = (await self._embed_texts([chunk_retrieval_content(chunk)]))[0]
        source = (
            "ctx._source.page_content = params.new_content; ctx._source.vector = params.new_vector;"
        )
        params: dict[str, Any] = {
            "new_content": chunk.page_content,
            "new_vector": vector,
        }
        if chunk_type == "qa":
            source += (
                " ctx._source.question = params.new_question;"
                " ctx._source.answer = params.new_answer;"
            )
            params["new_question"] = metadata.get("question", "")
            params["new_answer"] = metadata.get("answer", "")
        response = await self.client.update_by_query(
            index=self.index,
            script={"source": source, "params": params},
            query={"term": {Field.DOC_ID.value: metadata["doc_id"]}},
        )
        self._raise_on_failed_response(response, "segment update")
        return int(response.get("updated", 0))

    async def delete_by_ids(self, ids: list[str], *, refresh: bool = False) -> int:
        if not ids or not await self.client.indices.exists(index=self.index):
            return 0
        response = await self.client.delete_by_query(
            index=self.index,
            query={"terms": {Field.DOC_ID.value: ids}},
            refresh=False,
            conflicts="abort",
            wait_for_completion=True,
        )
        self._raise_on_failed_response(response, "segment delete")
        if refresh:
            await self.client.indices.refresh(index=self.index)
        return int(response.get("deleted", 0))

    async def _embed_chunks(self, chunks: list[DocumentChunk]) -> list[list[float] | None]:
        texts = []
        positions = []
        vectors: list[list[float] | None] = [None] * len(chunks)
        for index, chunk in enumerate(chunks):
            if (chunk.metadata or {}).get("chunk_type") in {"source", "parent"}:
                continue
            positions.append(index)
            texts.append(chunk_retrieval_content(chunk))
        if texts:
            embedded = await self._embed_texts(texts)
            for position, vector in zip(positions, embedded, strict=True):
                vectors[position] = vector
        return vectors

    async def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        if self.embed is None:
            raise RuntimeError("Embedding model is required for chunk mutation")
        return await self.embed(texts)

    async def _create_index(self, embeddings: list[list[float] | None]) -> None:
        sample = next((embedding for embedding in embeddings if embedding is not None), None)
        dimensions = len(sample) if sample is not None else 768
        await self.client.indices.create(
            index=self.index,
            mappings={
                "properties": {
                    "page_content": {"type": "text", "analyzer": "ik_max_word"},
                    "metadata": {
                        "type": "object",
                        "properties": {
                            "doc_id": {"type": "keyword"},
                            "file_id": {"type": "keyword"},
                            "file_name": {"type": "keyword"},
                            "file_created_at": {"type": "date", "format": "epoch_millis"},
                            "document_id": {"type": "keyword"},
                            "knowledge_id": {"type": "keyword"},
                            "sort_id": {"type": "long"},
                            "status": {"type": "integer"},
                            "parent_id": {"type": "keyword"},
                            "vision_text": {"type": "text", "analyzer": "ik_max_word"},
                        },
                    },
                    "vector": {
                        "type": "dense_vector",
                        "dims": dimensions,
                        "index": True,
                        "similarity": "cosine",
                    },
                    "chunk_type": {"type": "keyword"},
                    "question": {"type": "text", "analyzer": "ik_max_word"},
                    "answer": {"type": "text", "analyzer": "ik_max_word"},
                    "source_chunk_id": {"type": "keyword"},
                    "parent_id": {"type": "keyword"},
                }
            },
            settings={"index": {"refresh_interval": "1s"}},
        )

    @staticmethod
    def _raise_on_failed_response(response: Mapping[str, Any], operation: str) -> None:
        if response.get("timed_out") or response.get("failures"):
            raise RuntimeError(f"Elasticsearch {operation} failed")
        if response.get("_shards", {}).get("failed", 0):
            raise RuntimeError(f"Elasticsearch {operation} failed")


class AsyncElasticSearchRetrieval:
    """Native asynchronous vector and full-text retrieval."""

    def __init__(self, client: Any):
        self.client = client

    async def search_by_vector(
        self,
        embedding: AsyncEmbeddingClient,
        query: str,
        options: RetrievalSearchOptions,
    ) -> list[DocumentChunk]:
        vector = normalize_vector(await embedding.aembed_query(query))
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
        return await self.resolve_parent_chunks(
            vector_hits_to_chunks(response, options.score_threshold),
            options.indices,
        )

    async def search_by_full_text(
        self,
        query: str,
        options: RetrievalSearchOptions,
    ) -> list[DocumentChunk]:
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
        return await self.resolve_parent_chunks(
            full_text_hits_to_chunks(response, options.score_threshold),
            options.indices,
        )

    async def resolve_parent_chunks(
        self,
        chunks: list[DocumentChunk],
        index: str,
    ) -> list[DocumentChunk]:
        parent_ids = list(
            dict.fromkeys(
                str(chunk.metadata.get("parent_id"))
                for chunk in chunks
                if (chunk.metadata or {}).get("chunk_type") == "child"
                and chunk.metadata.get("parent_id")
            )
        )
        if not parent_ids:
            return chunks
        try:
            response = await self.client.search(
                index=index,
                size=len(parent_ids),
                query=build_parent_lookup_query(parent_ids),
                allow_partial_search_results=False,
            )
        except Exception:
            return chunks
        return merge_parent_chunks(chunks, (response.get("hits") or {}).get("hits", []))


__all__ = [
    "AsyncChunkStore",
    "AsyncElasticSearchRetrieval",
    "collection_name_for_knowledge",
]
