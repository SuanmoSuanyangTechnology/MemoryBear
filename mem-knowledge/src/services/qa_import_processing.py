"""Synchronous QA import processing for the dedicated Celery worker."""

from __future__ import annotations

import csv
import io
import logging
import time
import uuid
from dataclasses import dataclass

from ..models.owned import Document, Knowledge
from ..models.references import Workspace
from ..rag.models.chunk import DocumentChunk
from ..rag.models.task_runtime import TaskModelFactory
from ..rag.vdb.vector_store import TaskVectorStore
from ..runtime import ProcessRuntime
from ..tasks.state import PARSE_TASK_KEY
from ..utils.datetime_utils import to_iso_z, to_timestamp_ms, utcnow, utcnow_naive
from .knowledge_file_storage import KnowledgeFileStorage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _QAImportSnapshot:
    file_id: str
    file_name: str
    file_created_at: int | None
    embedding_id: uuid.UUID
    tenant_id: uuid.UUID


def _progress_ts() -> str:
    value = to_iso_z(utcnow())
    assert value is not None
    return value


def _progress_message(lines: list[str]) -> str:
    return "\n".join(lines) + "\n"


def _mark_running(
    runtime: ProcessRuntime,
    kb_id: uuid.UUID,
    document_id: uuid.UUID,
    progress_lines: list[str],
) -> _QAImportSnapshot | dict[str, object]:
    with runtime.database.sync_session() as session:
        document = session.get(Document, document_id)
        knowledge = session.get(Knowledge, kb_id)
        if document is None or knowledge is None:
            logger.error(
                "QA import document or knowledge not found: document=%s knowledge=%s",
                document_id,
                kb_id,
            )
            return {"error": "document or knowledge not found", "imported": 0}
        workspace = session.get(Workspace, knowledge.workspace_id)
        if workspace is None:
            raise ValueError("knowledge workspace not found")
        if knowledge.embedding_id is None:
            raise ValueError(f"embedding_id config error: {knowledge.id}")

        snapshot = _QAImportSnapshot(
            file_id=str(document.file_id),
            file_name=document.file_name,
            file_created_at=to_timestamp_ms(document.created_at),
            embedding_id=knowledge.embedding_id,
            tenant_id=workspace.tenant_id,
        )
        progress_lines.append(f"{_progress_ts()} Start to import QA.")
        document.progress = 0.0
        document.progress_msg = _progress_message(progress_lines)
        document.process_begin_at = utcnow_naive()
        document.process_duration = 0.0
        document.run = 1
        session.commit()
        return snapshot


def _load_contents(
    runtime: ProcessRuntime,
    contents: bytes | None,
    file_key: str | None,
) -> bytes:
    if contents is not None:
        return contents
    if not file_key:
        raise ValueError("contents or file_key is required for QA import")
    storage = KnowledgeFileStorage(runtime.storage)
    downloaded = runtime.run_async(lambda: storage.download(file_key))
    if not downloaded:
        raise OSError("Downloaded empty QA file from storage")
    return downloaded


def _parse_csv(contents: bytes) -> tuple[list[dict[str, str]], list[int]]:
    try:
        text = contents.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = contents.decode("gbk", errors="ignore")
    try:
        delimiter = csv.Sniffer().sniff(text[:2048]).delimiter
    except csv.Error:
        delimiter = "," if "," in text[:500] else "\t"

    pairs = []
    failed_rows = []
    for index, row in enumerate(csv.reader(io.StringIO(text), delimiter=delimiter)):
        if index == 0:
            continue
        if len(row) >= 2 and row[0].strip():
            pairs.append(
                {
                    "question": row[0].strip(),
                    "answer": row[1].strip() if row[1].strip() else "",
                }
            )
        elif len(row) >= 1 and row[0].strip():
            failed_rows.append(index + 1)
    return pairs, failed_rows


def _parse_excel(contents: bytes) -> tuple[list[dict[str, str]], list[int]]:
    import openpyxl

    pairs = []
    failed_rows = []
    try:
        workbook = openpyxl.load_workbook(io.BytesIO(contents), read_only=True)
        try:
            for sheet in workbook.worksheets:
                for index, row in enumerate(sheet.iter_rows(values_only=True)):
                    if index == 0:
                        continue
                    if len(row) >= 2 and row[0]:
                        question = str(row[0]).strip()
                        answer = str(row[1]).strip() if row[1] else ""
                        if question:
                            pairs.append({"question": question, "answer": answer})
                    elif len(row) >= 1 and row[0]:
                        failed_rows.append(index + 1)
        finally:
            workbook.close()
    except Exception as exc:
        raise RuntimeError(f"Excel parse failed: {exc}") from exc
    return pairs, failed_rows


def _parse_qa_file(
    filename: str,
    contents: bytes,
) -> tuple[list[dict[str, str]], list[int]]:
    if filename.endswith(".csv"):
        return _parse_csv(contents)
    if filename.endswith(".xlsx") or filename.endswith(".xls"):
        return _parse_excel(contents)
    return [], []


def _build_chunks(
    pairs: list[dict[str, str]],
    snapshot: _QAImportSnapshot,
    *,
    kb_id: str,
    document_id: str,
    sort_id: int,
) -> list[DocumentChunk]:
    chunks = []
    for pair in pairs:
        sort_id += 1
        metadata = {
            "doc_id": uuid.uuid4().hex,
            "file_id": snapshot.file_id,
            "file_name": snapshot.file_name,
            "file_created_at": snapshot.file_created_at,
            "document_id": document_id,
            "knowledge_id": kb_id,
            "sort_id": sort_id,
            "status": 1,
            "chunk_type": "qa",
            "question": pair["question"],
            "answer": pair["answer"],
        }
        chunks.append(DocumentChunk(page_content=pair["question"], metadata=metadata))
    return chunks


def _mark_complete(
    runtime: ProcessRuntime,
    document_id: uuid.UUID,
    chunk_count: int,
    clear_parse_task: bool,
    start_time: float,
    progress_lines: list[str],
) -> dict[str, object] | None:
    with runtime.database.sync_session() as session:
        document = session.get(Document, document_id)
        if document is None:
            logger.warning(
                "QA import document not found while completing: document=%s",
                document_id,
            )
            return {"error": "document not found", "imported": 0}
        if clear_parse_task:
            document.chunk_num = 0
        document.chunk_num += chunk_count
        document.progress = 1.0
        document.process_duration = time.time() - start_time
        document.run = 0
        progress_lines.append(f"{_progress_ts()} QA import done: {chunk_count} chunks.")
        document.progress_msg = _progress_message(progress_lines)
        session.commit()
    return None


def _mark_failed(
    runtime: ProcessRuntime,
    document_id: uuid.UUID,
    exc: Exception,
    start_time: float,
    progress_lines: list[str],
) -> None:
    try:
        with runtime.database.sync_session() as session:
            document = session.get(Document, document_id)
            if document is None:
                return
            progress_lines.append(f"{_progress_ts()} QA import failed: {str(exc)[:200]}")
            document.progress = -1.0
            document.progress_msg = _progress_message(progress_lines)
            document.process_duration = time.time() - start_time
            document.run = 0
            session.commit()
    except Exception:
        logger.warning(
            "Failed to persist QA import failure state: document=%s",
            document_id,
        )


def process_qa_import(
    runtime: ProcessRuntime,
    kb_id: str | uuid.UUID,
    document_id: str | uuid.UUID,
    filename: str,
    contents: bytes | None = None,
    file_key: str | None = None,
    clear_parse_task: bool = False,
) -> dict[str, object]:
    """Import QA rows while preserving the legacy task result and document state."""

    start_time = time.time()
    progress_lines = [f"{_progress_ts()} QA import task has been received."]
    normalized_document_id: uuid.UUID | None = None
    try:
        normalized_document_id = uuid.UUID(str(document_id))
        normalized_kb_id = uuid.UUID(str(kb_id))
        snapshot = _mark_running(
            runtime,
            normalized_kb_id,
            normalized_document_id,
            progress_lines,
        )
        if isinstance(snapshot, dict):
            return snapshot

        loaded_contents = _load_contents(runtime, contents, file_key)
        pairs, failed_rows = _parse_qa_file(filename, loaded_contents)
        if not pairs:
            logger.warning("No valid QA pairs found: document=%s", normalized_document_id)
            raise ValueError("No valid QA pairs found")
        progress_lines.append(f"{_progress_ts()} Parsed {len(pairs)} QA pairs.")

        embeddings = TaskModelFactory(runtime).create_embeddings(
            snapshot.embedding_id,
            snapshot.tenant_id,
        )
        vector_store = TaskVectorStore(
            runtime.elasticsearch.sync_client(),
            normalized_kb_id,
            embeddings,
        )
        sort_id = 0
        if clear_parse_task:
            vector_store.delete_by_metadata_field(
                "document_id",
                str(normalized_document_id),
            )
        else:
            _, items = vector_store.search_by_segment(
                document_id=str(normalized_document_id),
                pagesize=1,
                page=1,
                asc=False,
            )
            if items:
                sort_id = items[0].metadata["sort_id"]

        chunks = _build_chunks(
            pairs,
            snapshot,
            kb_id=str(normalized_kb_id),
            document_id=str(normalized_document_id),
            sort_id=sort_id,
        )
        batch_size = min(runtime.settings.embedding_batch_size or 10, 20)
        for start in range(0, len(chunks), batch_size):
            vector_store.add_chunks(chunks[start : start + batch_size])

        completion_error = _mark_complete(
            runtime,
            normalized_document_id,
            len(chunks),
            clear_parse_task,
            start_time,
            progress_lines,
        )
        if completion_error is not None:
            return completion_error
        logger.info(
            "QA import completed: document=%s imported=%s failed=%s",
            normalized_document_id,
            len(chunks),
            len(failed_rows),
        )
        return {"imported": len(chunks), "failed_rows": failed_rows}
    except Exception as exc:
        logger.error(
            "QA import failed: document=%s",
            normalized_document_id or document_id,
        )
        if normalized_document_id is not None:
            _mark_failed(
                runtime,
                normalized_document_id,
                exc,
                start_time,
                progress_lines,
            )
        return {"error": str(exc), "imported": 0}
    finally:
        if clear_parse_task:
            try:
                runtime.redis.sync_client().delete(
                    PARSE_TASK_KEY.format(
                        doc_id=normalized_document_id or document_id
                    )
                )
            except Exception:
                logger.warning(
                    "Failed to clear QA parse state: document=%s",
                    normalized_document_id,
                )


__all__ = ["process_qa_import"]
