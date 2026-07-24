import logging
import os
from collections.abc import Mapping, Sequence
from typing import Any

from app.core.rag.models.chunk import DocumentChunk
from app.core.rag.vdb.field import Field


logger = logging.getLogger(__name__)

VECTOR_SEARCH_MODE_ENV = "ELASTICSEARCH_VECTOR_SEARCH_MODE"
VECTOR_SEARCH_MODE_KNN = "knn"
VECTOR_SEARCH_MODE_SCRIPT_SCORE = "script_score"


def normalize_vector(vector: Any) -> list[float]:
    if hasattr(vector, "tolist"):
        return vector.tolist()
    return list(vector)


def _build_filter_clauses(
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
    if document_ids_include:
        filters.append({"terms": {Field.DOCUMENT_ID.value: list(document_ids_include)}})
    return filters


def build_vector_filter_clauses(
    file_names_filter: Sequence[str] | None,
    document_ids_include: Sequence[str] | None,
) -> list[dict[str, Any]]:
    return _build_filter_clauses(
        file_names_filter,
        document_ids_include,
        require_vector=True,
    )


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
            "filter": _build_filter_clauses(
                file_names_filter,
                document_ids_include,
                require_vector=False,
            ),
        }
    }


def resolve_knn_num_candidates(top_k: int, configured: Any = None) -> int:
    raw_value = configured if configured is not None else os.getenv("ELASTICSEARCH_KNN_NUM_CANDIDATES")
    if raw_value is not None:
        try:
            return max(int(raw_value), top_k)
        except (TypeError, ValueError):
            logger.warning("Invalid ELASTICSEARCH_KNN_NUM_CANDIDATES value: %s", raw_value)
    return max(top_k * 10, 100)


def resolve_vector_search_mode() -> str:
    raw_value = os.getenv(VECTOR_SEARCH_MODE_ENV, VECTOR_SEARCH_MODE_SCRIPT_SCORE)
    mode = raw_value.strip().lower()
    if mode == VECTOR_SEARCH_MODE_KNN:
        return VECTOR_SEARCH_MODE_KNN
    if mode in ("", VECTOR_SEARCH_MODE_SCRIPT_SCORE):
        return VECTOR_SEARCH_MODE_SCRIPT_SCORE

    logger.warning("Invalid %s value: %s, using script_score", VECTOR_SEARCH_MODE_ENV, raw_value)
    return VECTOR_SEARCH_MODE_SCRIPT_SCORE


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
                        "source": f"cosineSimilarity(params.query_vector, '{Field.VECTOR.value}') + 1.0",
                        "params": {"query_vector": list(query_vector)},
                    },
                }
            },
            "filter": list(filters),
        }
    }


def build_knn_query(
    query_vector: Sequence[float],
    top_k: int,
    filters: Sequence[dict[str, Any]],
    knn_num_candidates: Any = None,
) -> dict[str, Any]:
    return {
        "field": Field.VECTOR.value,
        "query_vector": list(query_vector),
        "k": top_k,
        "num_candidates": resolve_knn_num_candidates(top_k, knn_num_candidates),
        "filter": list(filters),
    }


def raise_on_shard_failures(result: Mapping[str, Any], context: str) -> None:
    shards = result.get("_shards") or {}
    failed = int(shards.get("failed") or 0)
    if failed <= 0:
        return

    failures = shards.get("failures") or []
    failure_summary = []
    for failure in failures[:3]:
        index = failure.get("index")
        reason = failure.get("reason") or {}
        reason_type = reason.get("type")
        reason_text = reason.get("reason")
        failure_summary.append(f"index={index}, type={reason_type}, reason={reason_text}")
    raise ValueError(
        f"Elasticsearch shard failures during {context}: failed={failed}, "
        f"failures={failure_summary}"
    )


def vector_hits_to_chunks(
    result: Mapping[str, Any],
    score_threshold: float | None,
    *,
    normalize_script_score: bool,
) -> list[DocumentChunk]:
    if "errors" in result:
        raise ValueError(f"Error during query: {result['errors']}")

    docs: list[DocumentChunk] = []
    for hit in result["hits"]["hits"]:
        source = hit["_source"]
        page_content = source.get(Field.CONTENT_KEY.value)
        metadata = dict(source.get(Field.METADATA_KEY.value) or {})
        chunk_type = source.get(Field.CHUNK_TYPE.value)
        score = hit["_score"] / 2 if normalize_script_score else hit["_score"]

        if chunk_type == "qa":
            question = source.get(Field.QUESTION.value, "")
            answer = source.get(Field.ANSWER.value, "")
            page_content = f"question: {question}\nanswer: {answer}"
            metadata["chunk_type"] = "qa"
            metadata["question"] = question
            metadata["answer"] = answer

        if score_threshold is None or score > score_threshold:
            metadata["score"] = score
            docs.append(DocumentChunk(page_content=page_content, metadata=metadata))
    return docs


def full_text_hits_to_chunks(
    result: Mapping[str, Any],
    score_threshold: float | None,
) -> list[DocumentChunk]:
    if "errors" in result:
        raise ValueError(f"Error during query: {result['errors']}")

    hits = result["hits"]
    max_score = hits["max_score"] or 1.0
    docs: list[DocumentChunk] = []
    for hit in hits["hits"]:
        source = hit["_source"]
        page_content = source.get(Field.CONTENT_KEY.value)
        metadata = dict(source.get(Field.METADATA_KEY.value) or {})
        chunk_type = source.get(Field.CHUNK_TYPE.value)

        if chunk_type == "qa":
            question = source.get(Field.QUESTION.value, "")
            answer = source.get(Field.ANSWER.value, "")
            page_content = f"question: {question}\nanswer: {answer}"
            metadata["chunk_type"] = "qa"
            metadata["question"] = question
            metadata["answer"] = answer

        normalized_score = hit["_score"] / max_score
        if score_threshold is None or normalized_score > score_threshold:
            metadata["score"] = normalized_score
            docs.append(DocumentChunk(page_content=page_content, metadata=metadata))
    return docs


def build_parent_lookup_query(parent_ids: Sequence[str]) -> dict[str, Any]:
    return {
        "bool": {
            "must": [
                {"terms": {Field.DOC_ID.value: list(parent_ids)}},
            ]
        }
    }


def merge_parent_chunks(
    chunks: list[DocumentChunk],
    parent_hits: Sequence[Mapping[str, Any]],
) -> list[DocumentChunk]:
    child_results = []
    other_results = []
    for doc in chunks:
        if (doc.metadata or {}).get("chunk_type") == "child":
            child_results.append(doc)
        else:
            other_results.append(doc)

    if not child_results:
        return chunks
    if not any(doc.metadata.get("parent_id") for doc in child_results):
        return chunks

    parent_map: dict[str, DocumentChunk] = {}
    for hit in parent_hits:
        source = hit["_source"]
        metadata = dict(source.get(Field.METADATA_KEY.value) or {})
        parent_doc_id = metadata.get("doc_id", "")
        parent_map[parent_doc_id] = DocumentChunk(
            page_content=source.get(Field.CONTENT_KEY.value, ""),
            metadata=metadata,
        )

    seen_parents: dict[str, DocumentChunk] = {}
    for doc in child_results:
        parent_id = doc.metadata.get("parent_id", "")
        if parent_id in seen_parents:
            existing = seen_parents[parent_id]
            if doc.metadata.get("score", 0) > existing.metadata.get("score", 0):
                seen_parents[parent_id] = DocumentChunk(
                    page_content=existing.page_content,
                    metadata={**existing.metadata, "score": doc.metadata.get("score", 0)},
                )
            continue

        parent = parent_map.get(parent_id)
        if parent:
            score = doc.metadata.get("score", 0)
            seen_parents[parent_id] = DocumentChunk(
                page_content=parent.page_content,
                metadata={**parent.metadata, "score": score, "chunk_type": "parent"},
            )
        else:
            seen_parents[parent_id] = doc

    return list(seen_parents.values()) + other_results
