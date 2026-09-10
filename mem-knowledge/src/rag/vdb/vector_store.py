"""Minimal synchronous Elasticsearch write surface for worker tasks."""

from __future__ import annotations

import math
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from elasticsearch import NotFoundError
from elasticsearch.helpers import bulk
from redbear_model import (
    EmbeddingPurpose,
    EmbeddingRequest,
    ImageEmbeddingContent,
    TextEmbeddingContent,
)

from ..models.chunk import DocumentChunk, chunk_retrieval_content
from ..models.embedding import collect_asset_file_ids
from ..models.retrieval_unit import (
    RetrievalUnit,
    RetrievalUnitKind,
    build_retrieval_units,
)
from .field import Field

ES_DEFAULT_MAX_RESULT_WINDOW = 10000
ImageResolver = Callable[..., dict[str, ImageEmbeddingContent]]


@dataclass(frozen=True)
class PreparedChunkBatch:
    """Fully embedded Elasticsearch actions that have not been written yet."""

    actions: tuple[dict[str, Any], ...]
    chunk_count: int


def collection_name_for_knowledge(knowledge_id: uuid.UUID | str) -> str:
    return f"Vector_index_{knowledge_id}_Node".lower()


class TaskVectorStore:
    """Write chunks and perform only task-side metadata operations."""

    def __init__(
        self,
        client: Any,
        knowledge_id: uuid.UUID | str,
        embeddings: Any,
        *,
        structured_multimodal: bool = False,
        image_resolver: ImageResolver | None = None,
        embedding_dimension: int | None = None,
    ):
        self._client = client
        self._collection_name = collection_name_for_knowledge(knowledge_id)
        self._embeddings = embeddings
        self._structured_multimodal = structured_multimodal
        self._image_resolver = image_resolver
        self._embedding_dimension = embedding_dimension

    def add_chunks(self, chunks: list[DocumentChunk]) -> None:
        if not chunks:
            return
        self.write_prepared_batches([self.prepare_chunks(chunks)])

    def prepare_chunks(self, chunks: list[DocumentChunk]) -> PreparedChunkBatch:
        """Embed and align one batch without performing Elasticsearch writes."""

        if not chunks:
            return PreparedChunkBatch(actions=(), chunk_count=0)
        if self._structured_multimodal:
            return self._prepare_multimodal_units(chunks)
        vectors = self._embed_chunks(chunks)
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
        return PreparedChunkBatch(actions=tuple(actions), chunk_count=len(chunks))

    def _prepare_multimodal_units(self, chunks: list[DocumentChunk]) -> PreparedChunkBatch:
        """Expand each chunk into retrieval units and embed text/image units."""

        requested_ids = collect_asset_file_ids(chunks)
        images = (
            self._image_resolver(requested_ids, phase="index")
            if requested_ids and self._image_resolver is not None
            else {}
        )
        units_with_vectors: list[tuple[RetrievalUnit, list[float] | None]] = []
        for chunk in chunks:
            units = build_retrieval_units(chunk, images)
            vectors = self._embed_units(units, chunk, images)
            if len(vectors) != len(units):
                raise RuntimeError("Unit embedding count does not match unit count")
            units_with_vectors.extend(zip(units, vectors, strict=True))
        actions = []
        for unit, vector in units_with_vectors:
            source: dict[str, Any] = {
                Field.UNIT_ID.value: unit.unit_id,
                Field.UNIT_KIND.value: unit.kind.value,
                Field.UNIT_INDEX.value: unit.unit_index,
                Field.CHUNK_ID.value: unit.chunk_id,
                Field.RETURN_CHUNK_ID.value: unit.return_chunk_id,
                Field.CONTENT_KEY.value: unit.content,
                Field.METADATA_KEY.value: unit.metadata,
                Field.VECTOR.value: vector,
            }
            if unit.asset_file_id is not None:
                source[Field.ASSET_FILE_ID.value] = unit.asset_file_id
            for field in (
                Field.CHUNK_TYPE,
                Field.QUESTION,
                Field.ANSWER,
                Field.SOURCE_CHUNK_ID,
                Field.PARENT_ID,
            ):
                if unit.metadata.get(field.value):
                    source[field.value] = unit.metadata[field.value]
            actions.append({"_id": unit.unit_id, "_index": self._collection_name, "_source": source})
        return PreparedChunkBatch(actions=tuple(actions), chunk_count=len(units_with_vectors))

    def _embed_units(
        self,
        units: list[RetrievalUnit],
        chunk: DocumentChunk,
        images: Mapping[str, ImageEmbeddingContent],
    ) -> list[list[float] | None]:
        """Embed each text/image unit with a single-content fusion request."""

        vectors: list[list[float] | None] = [None] * len(units)
        for index, unit in enumerate(units):
            contents = self._unit_embedding_contents(unit, images)
            if not contents:
                continue
            result = self._embeddings.embed_contents(
                EmbeddingRequest(purpose=EmbeddingPurpose.INDEX, contents=contents)
            )
            vector = list(result.vector)
            expected = self._embedding_dimension or result.dimension
            if len(vector) != expected or not all(math.isfinite(v) for v in vector):
                raise RuntimeError("Embedding result has an invalid vector")
            vectors[index] = vector
        return vectors

    @staticmethod
    def _unit_embedding_contents(
        unit: RetrievalUnit,
        images: Mapping[str, ImageEmbeddingContent],
    ) -> tuple:
        if unit.kind is RetrievalUnitKind.TEXT:
            return (TextEmbeddingContent(text=unit.content),) if unit.content.strip() else ()
        if unit.kind is RetrievalUnitKind.IMAGE:
            image = images.get(unit.asset_file_id or "")
            return (image,) if image is not None else ()
        return ()

    def write_prepared_batches(self, batches: list[PreparedChunkBatch]) -> None:
        """Validate every prepared batch before the first Elasticsearch write."""

        if not batches:
            return
        for batch in batches:
            if len(batch.actions) != batch.chunk_count:
                raise RuntimeError("Prepared chunk count does not match action count")
        if not any(batch.actions for batch in batches):
            return
        if not self._client.indices.exists(index=self._collection_name):
            sample = next(
                (
                    action["_source"][Field.VECTOR.value]
                    for batch in batches
                    for action in batch.actions
                    if action["_source"][Field.VECTOR.value] is not None
                ),
                None,
            )
            self._create_collection(sample)
        for batch in batches:
            if batch.actions:
                bulk(self._client, list(batch.actions))

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
        """Embed text chunks for the non-multimodal (plain text) write path."""

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

    def _create_collection(self, sample: list[float] | None) -> None:
        dimensions = (
            len(sample)
            if sample is not None
            else (self._embedding_dimension or 768)
        )
        # Both plain-text and multimodal (unit) indexes use HNSW now; multimodal
        # units are per-unit 2048-dim vectors, no longer a fused non-indexed blob.
        vector_mapping: dict[str, Any] = {
            "type": "dense_vector",
            "dims": dimensions,
            "index": True,
            "similarity": "cosine",
        }
        properties: dict[str, Any] = {
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
                    "asset_file_ids": {"type": "keyword"},
                    "vision_text": {
                        "type": "text",
                        "analyzer": "ik_max_word",
                    },
                },
            },
            Field.VECTOR.value: vector_mapping,
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
        if self._structured_multimodal:
            properties[Field.UNIT_ID.value] = {"type": "keyword"}
            properties[Field.UNIT_KIND.value] = {"type": "keyword"}
            properties[Field.UNIT_INDEX.value] = {"type": "long"}
            properties[Field.CHUNK_ID.value] = {"type": "keyword"}
            properties[Field.RETURN_CHUNK_ID.value] = {"type": "keyword"}
            properties[Field.ASSET_FILE_ID.value] = {"type": "keyword"}
        self._client.indices.create(
            index=self._collection_name,
            mappings={"properties": properties},
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


__all__ = [
    "PreparedChunkBatch",
    "TaskVectorStore",
    "collection_name_for_knowledge",
]
