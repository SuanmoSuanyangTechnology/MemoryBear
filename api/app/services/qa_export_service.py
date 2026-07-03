import csv
import io
import uuid
from collections.abc import Iterator
from typing import Any

from app.core.rag.vdb.elasticsearch.elasticsearch_vector import (
    ElasticSearchVectorClientProvider,
    ElasticSearchVectorIndexOps,
)
from app.core.rag.vdb.field import Field


QA_EXPORT_COLUMNS = ("question", "answer")
QA_EXPORT_BATCH_SIZE = 1000


def make_qa_export_filename(knowledge_name: str | None) -> str:
    safe_name = (knowledge_name or "").strip().replace("/", "_").replace("\\", "_")
    if not safe_name:
        return "qa_export.csv"
    return f"{safe_name}_qa_export.csv"


def iter_qa_pairs_by_knowledge(
    kb_id: uuid.UUID | str,
    batch_size: int = QA_EXPORT_BATCH_SIZE,
) -> Iterator[dict[str, str]]:
    kb_id_str = str(kb_id)
    batch_size = max(1, batch_size)
    index_name = ElasticSearchVectorIndexOps.collection_name_for_knowledge(kb_id_str)
    client = ElasticSearchVectorClientProvider.get_shared_client()

    if not client.indices.exists(index=index_name):
        return

    search_after: list[Any] | None = None
    while True:
        body = _build_qa_export_search_body(kb_id_str, batch_size, search_after)
        result = client.search(index=index_name, body=body)
        hits = result.get("hits", {}).get("hits", [])
        if not hits:
            return

        for hit in hits:
            source = hit.get("_source") or {}
            yield {
                "question": _normalize_csv_value(source.get(Field.QUESTION.value)),
                "answer": _normalize_csv_value(source.get(Field.ANSWER.value)),
            }

        search_after = hits[-1].get("sort")
        if not search_after:
            return


def iter_qa_csv_chunks(
    kb_id: uuid.UUID | str,
    batch_size: int = QA_EXPORT_BATCH_SIZE,
) -> Iterator[bytes]:
    yield _write_csv_rows([QA_EXPORT_COLUMNS]).encode("utf-8-sig")
    for pair in iter_qa_pairs_by_knowledge(kb_id=kb_id, batch_size=batch_size):
        yield _write_csv_rows([(pair["question"], pair["answer"])]).encode("utf-8")


def _build_qa_export_search_body(
    kb_id: str,
    batch_size: int,
    search_after: list[Any] | None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "query": {
            "bool": {
                "filter": [
                    {"term": {Field.CHUNK_TYPE.value: "qa"}},
                    {"term": {Field.KNOWLEDGE_ID.value: kb_id}},
                    {"term": {"metadata.status": 1}},
                ]
            }
        },
        "_source": [Field.QUESTION.value, Field.ANSWER.value],
        "size": batch_size,
        "sort": [
            {
                Field.DOCUMENT_ID.value: {
                    "order": "asc",
                    "unmapped_type": "keyword",
                    "missing": "_last",
                }
            },
            {
                Field.SORT_ID.value: {
                    "order": "asc",
                    "unmapped_type": "long",
                    "missing": "_last",
                }
            },
            {
                Field.DOC_ID.value: {
                    "order": "asc",
                    "unmapped_type": "keyword",
                    "missing": "_last",
                }
            },
        ],
    }
    if search_after:
        body["search_after"] = search_after
    return body


def _write_csv_rows(rows: list[tuple[str, ...]]) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerows(rows)
    return output.getvalue()


def _normalize_csv_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value)
