"""Minimal synchronous Elasticsearch write surface for worker tasks."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from elasticsearch import NotFoundError
from elasticsearch.helpers import bulk

from ..models.chunk import DocumentChunk, chunk_retrieval_content
from .field import Field

ES_DEFAULT_MAX_RESULT_WINDOW = 10000


def collection_name_for_knowledge(knowledge_id: uuid.UUID | str) -> str:
    return f"Vector_index_{knowledge_id}_Node".lower()


class TaskVectorStore:
    """Write chunks and perform only task-side metadata operations."""

    def __init__(self, client: Any, knowledge_id: uuid.UUID | str, embeddings: Any):
        self._client = client
        self._collection_name = collection_name_for_knowledge(knowledge_id)
        self._embeddings = embeddings

    def add_chunks(self, chunks: list[DocumentChunk]) -> None:
        if not chunks:
            return
        vectors = self._embed_chunks(chunks)
        if not self._client.indices.exists(index=self._collection_name):
            self._create_collection(vectors)

        actions = []
        for chunk, vector in zip(chunks, vectors, strict=True):
            metadata = dict(chunk.metadata or {})
            source: dict[str, Any] = {
                Field.CONTENT_KEY.value: chunk.page_content,
                Field.METADATA_KEY.value: metadata,
                Field.VECTOR.value: vector,
            }
            for field in (
                Field.CHUNK_TYPE,
                Field.QUESTION,
                Field.ANSWER,
                Field.SOURCE_CHUNK_ID,
                Field.PARENT_ID,
            ):
                if metadata.get(field.value):
                    source[field.value] = metadata[field.value]
            actions.append({"_index": self._collection_name, "_source": source})
        bulk(self._client, actions)

    def delete_by_metadata_field(
        self,
        key: str,
        value: str,
        *,
        refresh: bool = False,
    ) -> bool:
        if not self._client.indices.exists(index=self._collection_name):
            return False
        response = self._client.delete_by_query(
            index=self._collection_name,
            query={"term": {f"{Field.METADATA_KEY.value}.{key}": value}},
            refresh=refresh,
            conflicts="abort",
            wait_for_completion=True,
        )
        self._raise_on_failed_response(response, "metadata delete")
        return True

    def search_by_segment(
        self,
        document_id: str | None = None,
        query: str | None = None,
        pagesize: int = 10,
        page: int = 1,
        asc: bool = True,
        chunk_types: list[str] | str | None = None,
        parent_ids: list[str] | str | None = None,
    ) -> tuple[int, list[DocumentChunk]]:
        if not self._client.indices.exists(index=self._collection_name):
            return 0, []
        offset = pagesize * (page - 1)
        if offset + pagesize > ES_DEFAULT_MAX_RESULT_WINDOW:
            raise ValueError("Task segment search exceeds the Elasticsearch result window")
        try:
            response = self._client.search(
                index=self._collection_name,
                from_=offset,
                size=pagesize,
                query=self._build_segment_query(
                    document_id,
                    query,
                    chunk_types,
                    parent_ids,
                ),
                sort=self._segment_sort(asc),
                track_total_hits=True,
                allow_partial_search_results=False,
            )
        except NotFoundError:
            return 0, []
        self._raise_on_failed_response(response, "segment search")
        hits = response.get("hits", {}).get("hits", [])
        total = int(response.get("hits", {}).get("total", {}).get("value", 0))
        return total, [self._hit_to_chunk(hit) for hit in hits]

    def _embed_chunks(self, chunks: list[DocumentChunk]) -> list[list[float] | None]:
        positions = []
        texts = []
        vectors: list[list[float] | None] = [None] * len(chunks)
        for index, chunk in enumerate(chunks):
            if (chunk.metadata or {}).get("chunk_type") in {"source", "parent"}:
                continue
            positions.append(index)
            texts.append(chunk_retrieval_content(chunk))
        if not texts:
            return vectors
        supports_multimodal = getattr(
            self._embeddings,
            "is_multimodal_supported",
            lambda: False,
        )()
        if supports_multimodal:
            embedded = []
            for text in texts:
                result = list(self._embeddings.embed_batch([text]))
                self._validate_embedding_count(result, 1)
                embedded.append(result[0])
        else:
            embedded = list(self._embeddings.embed_documents(texts))
            self._validate_embedding_count(embedded, len(texts))
        for position, vector in zip(positions, embedded, strict=True):
            vectors[position] = vector
        return vectors

    @staticmethod
    def _validate_embedding_count(embedded: list[Any], expected: int) -> None:
        if len(embedded) != expected:
            raise RuntimeError("Embedding result count does not match input count")

    def _create_collection(self, vectors: list[list[float] | None]) -> None:
        sample = next((vector for vector in vectors if vector is not None), None)
        dimensions = len(sample) if sample is not None else 768
        self._client.indices.create(
            index=self._collection_name,
            mappings={
                "properties": {
                    Field.CONTENT_KEY.value: {
                        "type": "text",
                        "analyzer": "ik_max_word",
                    },
                    Field.METADATA_KEY.value: {
                        "type": "object",
                        "properties": {
                            "doc_id": {"type": "keyword"},
                            "file_id": {"type": "keyword"},
                            "file_name": {"type": "keyword"},
                            "file_created_at": {
                                "type": "date",
                                "format": "epoch_millis",
                            },
                            "document_id": {"type": "keyword"},
                            "knowledge_id": {"type": "keyword"},
                            "sort_id": {"type": "long"},
                            "status": {"type": "integer"},
                            "parent_id": {"type": "keyword"},
                            "vision_text": {
                                "type": "text",
                                "analyzer": "ik_max_word",
                            },
                        },
                    },
                    Field.VECTOR.value: {
                        "type": "dense_vector",
                        "dims": dimensions,
                        "index": True,
                        "similarity": "cosine",
                    },
                    Field.CHUNK_TYPE.value: {"type": "keyword"},
                    Field.QUESTION.value: {
                        "type": "text",
                        "analyzer": "ik_max_word",
                    },
                    Field.ANSWER.value: {
                        "type": "text",
                        "analyzer": "ik_max_word",
                    },
                    Field.SOURCE_CHUNK_ID.value: {"type": "keyword"},
                    Field.PARENT_ID.value: {"type": "keyword"},
                }
            },
            settings={"index": {"refresh_interval": "1s"}},
        )

    @staticmethod
    def _build_segment_query(
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
            must.append(
                {
                    "terms": {
                        f"{Field.METADATA_KEY.value}.{Field.PARENT_ID.value}": values
                    }
                }
            )
        return {"bool": {"must": must}}

    @staticmethod
    def _segment_sort(asc: bool) -> list[dict[str, Any]]:
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
    def _hit_to_chunk(hit: Mapping[str, Any]) -> DocumentChunk:
        source = hit.get("_source") or {}
        metadata = dict(source.get(Field.METADATA_KEY.value) or {})
        chunk_type = source.get(Field.CHUNK_TYPE.value)
        page_content = source.get(Field.CONTENT_KEY.value) or ""
        if chunk_type:
            metadata[Field.CHUNK_TYPE.value] = chunk_type
        if chunk_type == "qa":
            metadata[Field.QUESTION.value] = source.get(Field.QUESTION.value, "")
            metadata[Field.ANSWER.value] = source.get(Field.ANSWER.value, "")
            page_content = (
                f"question: {metadata[Field.QUESTION.value]}\n"
                f"answer: {metadata[Field.ANSWER.value]}"
            )
        metadata["score"] = hit.get("_score")
        return DocumentChunk(page_content=page_content, vector=None, metadata=metadata)

    @staticmethod
    def _raise_on_failed_response(response: Mapping[str, Any], operation: str) -> None:
        if response.get("timed_out") or response.get("failures"):
            raise RuntimeError(f"Elasticsearch {operation} failed")
        if response.get("_shards", {}).get("failed", 0):
            raise RuntimeError(f"Elasticsearch {operation} failed")


__all__ = ["TaskVectorStore", "collection_name_for_knowledge"]
