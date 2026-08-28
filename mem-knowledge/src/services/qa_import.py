"""QA import records and Celery message dispatch copied from the legacy route."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from ..api.dependencies import Principal
from ..api.schemas.document import DocumentCreate
from ..api.schemas.file import FileCreate
from ..errors import KnowledgeError
from ..models.owned import Document, File
from ..repositories import document as document_repository
from ..repositories import file as file_repository
from ..tasks.dispatch import TaskDispatcher
from . import file as file_service
from . import knowledge as knowledge_service
from .knowledge_file_storage import KnowledgeFileStorage, generate_kb_file_key

QA_EXTENSIONS = {".csv", ".xlsx", ".xls"}


@dataclass(frozen=True)
class QAImportResources:
    document: Document
    file: File


def validate_qa_upload(
    filename: str,
    content: bytes,
    *,
    require_non_empty: bool = True,
) -> str:
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix not in QA_EXTENSIONS:
        raise KnowledgeError.from_code(
            "KB_VALIDATION_ERROR",
            "Only CSV (.csv) or Excel (.xlsx/.xls) files are supported",
        )
    if require_non_empty and not content:
        raise KnowledgeError.from_code("KB_VALIDATION_ERROR", "QA import file is empty")
    return suffix


async def dispatch_qa_import(
    dispatcher: TaskDispatcher,
    kb_id: uuid.UUID,
    document_id: uuid.UUID,
    filename: str,
    content: bytes,
) -> str:
    return await dispatcher.send(
        "app.core.rag.tasks.import_qa_chunks",
        args=[str(kb_id), str(document_id), filename, content],
        queue="qa_import",
    )


async def create_qa_import_resources(
    db: AsyncSession,
    storage: KnowledgeFileStorage,
    *,
    kb_id: uuid.UUID,
    parent_id: uuid.UUID | None,
    filename: str,
    content: bytes,
    content_type: str | None,
    principal: Principal,
) -> QAImportResources:
    suffix = validate_qa_upload(filename, content)
    if await knowledge_service.get_knowledge(db, kb_id, principal) is None:
        raise KnowledgeError.from_code("KB_RESOURCE_NOT_FOUND", "Knowledge resource not found")
    await file_service.require_parent_folder(db, kb_id, parent_id, principal)
    db_file = await file_repository.create_file_async(
        db,
        FileCreate(
            kb_id=kb_id,
            created_by=principal.actor_id,
            parent_id=parent_id or kb_id,
            file_name=filename,
            file_ext=suffix,
            file_size=len(content),
        ),
    )
    file_key = generate_kb_file_key(kb_id, db_file.id, suffix)
    try:
        await storage.upload(file_key, content, content_type)
    except Exception:
        await file_repository.delete_file_by_id_async(db, db_file.id)
        raise
    try:
        db_file.file_key = file_key
        await db.commit()
        await db.refresh(db_file)
        document = await document_repository.create_document_async(
            db,
            DocumentCreate(
                kb_id=kb_id,
                created_by=principal.actor_id,
                file_id=db_file.id,
                file_name=filename,
                file_ext=suffix,
                file_size=len(content),
                file_meta={},
                parser_id="qa",
                parser_config={"doc_type": "qa", "auto_questions": 0},
            ),
        )
    except Exception:
        try:
            await storage.delete(file_key)
        finally:
            await file_repository.delete_file_by_id_async(db, db_file.id)
        raise
    return QAImportResources(document=document, file=db_file)


__all__ = [
    "QAImportResources",
    "create_qa_import_resources",
    "dispatch_qa_import",
    "validate_qa_upload",
]
