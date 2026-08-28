"""QA export behavior adapted from the legacy interface-side service."""

from __future__ import annotations

import asyncio
import csv
import os
import tempfile
import uuid
from collections.abc import AsyncIterator, Mapping
from typing import Any

import aiofiles

from ..rag.vdb.field import Field
from ..rag.vdb.pit_search import iter_async_search_after_hits

QA_EXPORT_COLUMNS = ("question", "answer")
QA_CSV_MEDIA_TYPE = "text/csv"
QA_XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def collection_name_for_knowledge(knowledge_id: uuid.UUID | str) -> str:
    return f"Vector_index_{knowledge_id}_Node".lower()


def make_qa_export_filename(knowledge_name: str | None) -> str:
    safe_name = (knowledge_name or "").strip().replace("/", "_").replace("\\", "_")
    return f"{safe_name}_qa_export.csv" if safe_name else "qa_export.csv"


def _qa_query(kb_id: str, document_id: str | None = None) -> dict[str, Any]:
    filters: list[dict[str, Any]] = [
        {
            "bool": {
                "should": [
                    {"term": {Field.CHUNK_TYPE.value: "qa"}},
                    {"term": {f"{Field.METADATA_KEY.value}.{Field.CHUNK_TYPE.value}": "qa"}},
                ],
                "minimum_should_match": 1,
            }
        },
        {"term": {Field.KNOWLEDGE_ID.value: kb_id}},
    ]
    if document_id is None:
        filters.append({"term": {"metadata.status": 1}})
    else:
        filters.append({"term": {Field.DOCUMENT_ID.value: document_id}})
    return {"bool": {"filter": filters}}


def _qa_sort() -> list[dict[str, Any]]:
    return [
        {Field.DOCUMENT_ID.value: {"order": "asc", "unmapped_type": "keyword", "missing": "_last"}},
        {Field.SORT_ID.value: {"order": "asc", "unmapped_type": "long", "missing": "_last"}},
        {Field.DOC_ID.value: {"order": "asc", "unmapped_type": "keyword", "missing": "_last"}},
    ]


def _pair(source: Mapping[str, Any]) -> dict[str, str]:
    metadata = source.get(Field.METADATA_KEY.value) or {}
    if not isinstance(metadata, Mapping):
        metadata = {}
    question = source.get(Field.QUESTION.value, metadata.get(Field.QUESTION.value))
    answer = source.get(Field.ANSWER.value, metadata.get(Field.ANSWER.value))
    return {
        "question": "" if question is None else str(question),
        "answer": "" if answer is None else str(answer),
    }


async def collect_qa_pairs(
    client: Any,
    kb_id: uuid.UUID | str,
    document_id: uuid.UUID | str | None = None,
) -> list[dict[str, str]]:
    index = collection_name_for_knowledge(kb_id)
    if not await client.indices.exists(index=index):
        return []
    pairs = []
    async for hit in iter_async_search_after_hits(
        client,
        index=index,
        query=_qa_query(str(kb_id), str(document_id) if document_id else None),
        sort=_qa_sort(),
        source_includes=[
            Field.QUESTION.value,
            Field.ANSWER.value,
            f"{Field.METADATA_KEY.value}.{Field.QUESTION.value}",
            f"{Field.METADATA_KEY.value}.{Field.ANSWER.value}",
        ],
    ):
        pairs.append(_pair(hit.get("_source") or {}))
    return pairs


def _write_csv_file(pairs: list[dict[str, str]], *, require_rows: bool) -> str | None:
    if require_rows and not pairs:
        return None
    descriptor, path = tempfile.mkstemp(prefix="memorybear-qa-export-", suffix=".csv")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8-sig", newline="") as output:
            writer = csv.writer(output)
            writer.writerow(QA_EXPORT_COLUMNS)
            for pair in pairs:
                writer.writerow([pair["question"], pair["answer"]])
    except Exception:
        cleanup_export_file(path)
        raise
    return path


def _write_xlsx_file(pairs: list[dict[str, str]]) -> str | None:
    if not pairs:
        return None
    import openpyxl

    descriptor, path = tempfile.mkstemp(prefix="memorybear-qa-export-", suffix=".xlsx")
    os.close(descriptor)
    workbook = openpyxl.Workbook(write_only=True)
    try:
        sheet = workbook.create_sheet()
        sheet.append(QA_EXPORT_COLUMNS)
        for pair in pairs:
            sheet.append([pair["question"], pair["answer"]])
        workbook.save(path)
        return path
    except Exception:
        cleanup_export_file(path)
        raise
    finally:
        workbook.close()


async def write_knowledge_csv(client: Any, kb_id: uuid.UUID | str) -> str:
    pairs = await collect_qa_pairs(client, kb_id)
    path = await asyncio.to_thread(_write_csv_file, pairs, require_rows=False)
    assert path is not None
    return path


async def write_document_export(
    client: Any,
    kb_id: uuid.UUID | str,
    document_id: uuid.UUID | str,
    file_ext: str | None,
) -> tuple[str, str] | None:
    pairs = await collect_qa_pairs(client, kb_id, document_id)
    if (file_ext or "").lower() in {".xlsx", ".xls"}:
        path = await asyncio.to_thread(_write_xlsx_file, pairs)
        return (path, QA_XLSX_MEDIA_TYPE) if path else None
    path = await asyncio.to_thread(_write_csv_file, pairs, require_rows=True)
    return (path, QA_CSV_MEDIA_TYPE) if path else None


async def iter_export_file(path: str, chunk_size: int = 1024 * 1024) -> AsyncIterator[bytes]:
    async with aiofiles.open(path, "rb") as file:
        while chunk := await file.read(chunk_size):
            yield chunk


def cleanup_export_file(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
