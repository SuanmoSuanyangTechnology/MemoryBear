import csv
import io
import os
import tempfile
import uuid
from collections.abc import Iterator, Mapping
from typing import Any

from app.core.rag.vdb.elasticsearch.elasticsearch_vector import (
    ElasticSearchVectorClientProvider,
    ElasticSearchVectorIndexOps,
)
from app.core.rag.vdb.elasticsearch.pit_search import iter_pit_search_hits
from app.core.rag.vdb.field import Field


QA_EXPORT_COLUMNS = ("question", "answer")
QA_EXPORT_BATCH_SIZE = 1000
QA_CSV_FILE_CHUNK_SIZE = 1024 * 1024
QA_CSV_MEDIA_TYPE = "text/csv"
QA_XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


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
    index_name = ElasticSearchVectorIndexOps.collection_name_for_knowledge(kb_id_str)
    client = ElasticSearchVectorClientProvider.get_shared_client()

    if not client.indices.exists(index=index_name):
        return

    for hit in iter_pit_search_hits(
        client,
        index=index_name,
        query=_qa_export_query(kb_id_str, active_only=True),
        source_includes=_qa_source_includes(),
        sort=_qa_export_sort(),
        batch_size=max(1, min(batch_size, 10000)),
    ):
        source = hit.get("_source") or {}
        yield _qa_pair_from_source(source)


def iter_qa_pairs_by_document(
    kb_id: uuid.UUID | str,
    document_id: uuid.UUID | str,
    batch_size: int = QA_EXPORT_BATCH_SIZE,
) -> Iterator[dict[str, str]]:
    kb_id_str = str(kb_id)
    index_name = ElasticSearchVectorIndexOps.collection_name_for_knowledge(kb_id_str)
    client = ElasticSearchVectorClientProvider.get_shared_client()

    if not client.indices.exists(index=index_name):
        return

    filters: list[dict[str, Any]] = [
        _qa_chunk_type_filter(),
        {"term": {Field.KNOWLEDGE_ID.value: kb_id_str}},
        {"term": {Field.DOCUMENT_ID.value: str(document_id)}},
    ]
    for hit in iter_pit_search_hits(
        client,
        index=index_name,
        query={"bool": {"filter": filters}},
        source_includes=_qa_source_includes(),
        sort=_qa_export_sort(),
        batch_size=max(1, min(batch_size, 10000)),
    ):
        source = hit.get("_source") or {}
        yield _qa_pair_from_source(source)


def write_qa_csv_export_file(
    kb_id: uuid.UUID | str,
    batch_size: int = QA_EXPORT_BATCH_SIZE,
) -> str:
    fd, path = tempfile.mkstemp(prefix="memorybear-qa-export-", suffix=".csv")
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as output:
            writer = csv.writer(output)
            writer.writerow(QA_EXPORT_COLUMNS)
            for pair in iter_qa_pairs_by_knowledge(kb_id=kb_id, batch_size=batch_size):
                writer.writerow([pair["question"], pair["answer"]])
    except Exception:
        _remove_file_if_exists(path)
        raise
    return path


def iter_qa_csv_file_chunks(
    path: str,
    chunk_size: int = QA_CSV_FILE_CHUNK_SIZE,
) -> Iterator[bytes]:
    with open(path, "rb") as file:
        while chunk := file.read(chunk_size):
            yield chunk


def cleanup_qa_csv_export_file(path: str) -> None:
    _remove_file_if_exists(path)


def render_qa_pairs_export(
    qa_pairs: list[Mapping[str, Any]],
    file_ext: str | None,
) -> tuple[bytes, str] | None:
    if not qa_pairs:
        return None

    if _is_xlsx_file(file_ext):
        return _write_xlsx_qa_pairs(qa_pairs), QA_XLSX_MEDIA_TYPE

    rows: list[tuple[str, ...]] = [QA_EXPORT_COLUMNS]
    rows.extend(
        (
            _normalize_csv_value(pair.get("question")),
            _normalize_csv_value(pair.get("answer")),
        )
        for pair in qa_pairs
    )
    return _write_csv_rows(rows).encode("utf-8-sig"), QA_CSV_MEDIA_TYPE


def _qa_export_sort() -> list[dict[str, Any]]:
    return [
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
    ]


def _qa_chunk_type_filter() -> dict[str, Any]:
    return {
        "bool": {
            "should": [
                {"term": {Field.CHUNK_TYPE.value: "qa"}},
                {"term": {f"{Field.METADATA_KEY.value}.{Field.CHUNK_TYPE.value}": "qa"}},
            ],
            "minimum_should_match": 1,
        }
    }


def _qa_source_includes() -> list[str]:
    return [
        Field.QUESTION.value,
        Field.ANSWER.value,
        f"{Field.METADATA_KEY.value}.{Field.QUESTION.value}",
        f"{Field.METADATA_KEY.value}.{Field.ANSWER.value}",
    ]


def _qa_export_query(kb_id: str, *, active_only: bool) -> dict[str, Any]:
    filters: list[dict[str, Any]] = [
        _qa_chunk_type_filter(),
        {"term": {Field.KNOWLEDGE_ID.value: kb_id}},
    ]
    if active_only:
        filters.append({"term": {"metadata.status": 1}})
    return {"bool": {"filter": filters}}


def _qa_pair_from_source(source: Mapping[str, Any]) -> dict[str, str]:
    metadata = source.get(Field.METADATA_KEY.value) or {}
    if not isinstance(metadata, Mapping):
        metadata = {}
    question = source.get(Field.QUESTION.value)
    answer = source.get(Field.ANSWER.value)
    if question is None:
        question = metadata.get(Field.QUESTION.value)
    if answer is None:
        answer = metadata.get(Field.ANSWER.value)
    return {
        "question": _normalize_csv_value(question),
        "answer": _normalize_csv_value(answer),
    }


def _write_csv_rows(rows: list[tuple[str, ...]]) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerows(rows)
    return output.getvalue()


def _is_xlsx_file(file_ext: str | None) -> bool:
    file_ext_lower = file_ext.lower() if file_ext else ".csv"
    return file_ext_lower in (".xlsx", ".xls")


def _write_xlsx_qa_pairs(qa_pairs: list[Mapping[str, Any]]) -> bytes:
    import openpyxl

    wb = openpyxl.Workbook()
    try:
        ws = wb.active
        ws.append(QA_EXPORT_COLUMNS)
        for pair in qa_pairs:
            ws.append(
                [
                    _normalize_csv_value(pair.get("question")),
                    _normalize_csv_value(pair.get("answer")),
                ]
            )
        output = io.BytesIO()
        wb.save(output)
        return output.getvalue()
    finally:
        wb.close()


def _remove_file_if_exists(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def _normalize_csv_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value)
