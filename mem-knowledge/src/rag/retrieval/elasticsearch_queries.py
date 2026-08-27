"""Elasticsearch retrieval query helpers copied from the legacy API."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ..models.chunk import DocumentChunk
from ..vdb.field import Field


def normalize_vector(vector: Any) -> list[float]:
    if hasattr(vector, "tolist"):
        return vector.tolist()
    return list(vector)


def build_filter_clauses(
    file_names_filter: Sequence[str] | None,
    document_ids_include: Sequence[str] | None,
    *,
    require_vector: bool,
) -> list[dict[str, Any]]:
    filters: list[dict[str, Any]] = [{"term": {"metadata.status": 1}}]
    if require_vector:
        filters.append({"exists": {"field": Field.VECTOR.value}})
    if file_names_filter:
        filters.append({"terms": {"metadata.file_name": list(file_names_filter)}})
    if document_ids_include is not None:
        filters.append({"terms": {Field.DOCUMENT_ID.value: list(document_ids_include)}})
    return filters


def build_full_text_query(
    query: str,
    file_names_filter: Sequence[str] | None,
    document_ids_include: Sequence[str] | None,
) -> dict[str, Any]:
    return {
        "bool": {
            "must": {
                "multi_match": {
                    "query": query,
                    "fields": [Field.CONTENT_KEY.value, Field.VISION_TEXT.value],
                    "analyzer": "ik_max_word",
                }
            },
            "filter": build_filter_clauses(
                file_names_filter,
                document_ids_include,
                require_vector=False,
            ),
        }
    }


def build_vector_script_query(
    query_vector: Sequence[float],
    filters: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "bool": {
            "must": {
                "script_score": {
                    "query": {"match_all": {}},
                    "script": {
                        "source": (
                            f"cosineSimilarity(params.query_vector, '{Field.VECTOR.value}') + 1.0"
                        ),
                        "params": {"query_vector": list(query_vector)},
                    },
                }
            },
            "filter": list(filters),
        }
    }


def raise_on_shard_failures(result: Mapping[str, Any], context: str) -> None:
    failed = int((result.get("_shards") or {}).get("failed") or 0)
    if failed:
        raise ValueError(f"Elasticsearch shard failures during {context}: failed={failed}")


def _hit_to_chunk(
    hit: Mapping[str, Any],
    score: float,
) -> DocumentChunk:
    source = hit.get("_source") or {}
    metadata = dict(source.get(Field.METADATA_KEY.value) or {})
    chunk_type = source.get(Field.CHUNK_TYPE.value)
    page_content = source.get(Field.CONTENT_KEY.value) or ""
    if chunk_type == "qa":
        question = source.get(Field.QUESTION.value, "")
        answer = source.get(Field.ANSWER.value, "")
        page_content = f"question: {question}\nanswer: {answer}"
        metadata.update(chunk_type="qa", question=question, answer=answer)
    metadata["score"] = score
    return DocumentChunk(page_content=page_content, metadata=metadata)


def vector_hits_to_chunks(
    result: Mapping[str, Any],
    score_threshold: float | None,
) -> list[DocumentChunk]:
    raise_on_shard_failures(result, "vector search")
    chunks = []
    for hit in (result.get("hits") or {}).get("hits", []):
        score = float(hit.get("_score") or 0) / 2
        if score_threshold is None or score > score_threshold:
            chunks.append(_hit_to_chunk(hit, score))
    return chunks


def full_text_hits_to_chunks(
    result: Mapping[str, Any],
    score_threshold: float | None,
) -> list[DocumentChunk]:
    raise_on_shard_failures(result, "full text search")
    hits = result.get("hits") or {}
    max_score = float(hits.get("max_score") or 1)
    chunks = []
    for hit in hits.get("hits", []):
        score = float(hit.get("_score") or 0) / max_score
        if score_threshold is None or score > score_threshold:
            chunks.append(_hit_to_chunk(hit, score))
    return chunks


def build_parent_lookup_query(parent_ids: Sequence[str]) -> dict[str, Any]:
    return {"bool": {"must": [{"terms": {Field.DOC_ID.value: list(parent_ids)}}]}}


def merge_parent_chunks(
    chunks: list[DocumentChunk],
    parent_hits: Sequence[Mapping[str, Any]],
) -> list[DocumentChunk]:
    parent_map: dict[str, DocumentChunk] = {}
    for hit in parent_hits:
        source = hit.get("_source") or {}
        metadata = dict(source.get(Field.METADATA_KEY.value) or {})
        parent_map[str(metadata.get("doc_id") or "")] = DocumentChunk(
            page_content=source.get(Field.CONTENT_KEY.value) or "",
            metadata=metadata,
        )
    resolved: list[DocumentChunk] = []
    seen_parents: set[str] = set()
    for chunk in chunks:
        metadata = chunk.metadata or {}
        if metadata.get("chunk_type") != "child" or not metadata.get("parent_id"):
            resolved.append(chunk)
            continue
        parent_id = str(metadata["parent_id"])
        if parent_id in seen_parents:
            continue
        seen_parents.add(parent_id)
        parent = parent_map.get(parent_id)
        if parent is None:
            resolved.append(chunk)
            continue
        parent.metadata.update(score=metadata.get("score", 0), chunk_type="parent")
        resolved.append(parent)
    return resolved


__all__ = [
    "build_filter_clauses",
    "build_full_text_query",
    "build_parent_lookup_query",
    "build_vector_script_query",
    "full_text_hits_to_chunks",
    "merge_parent_chunks",
    "normalize_vector",
    "vector_hits_to_chunks",
]
