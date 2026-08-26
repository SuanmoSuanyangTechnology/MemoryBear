"""QA import records and Celery message dispatch copied from the legacy route."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from ..api.dependencies import Principal
from ..errors import KnowledgeError
from ..tasks.dispatch import TaskDispatcher
from . import file as file_service
from .knowledge_file_storage import KnowledgeFileStorage

QA_EXTENSIONS = {".csv", ".xlsx", ".xls"}


@dataclass(frozen=True)
class QAImportResources:
    document_id: uuid.UUID
    file_id: uuid.UUID


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


async def prepare_qa_import_resources(
    db: AsyncSession,
    storage: KnowledgeFileStorage,
    *,
    kb_id: uuid.UUID,
    parent_id: uuid.UUID | None,
    filename: str,
    content: bytes,
    content_type: str | None,
    principal: Principal,
) -> file_service.UploadPlan:
    suffix = validate_qa_upload(filename, content)
    return await file_service.upload_content(
        db,
        storage,
        kb_id=kb_id,
        parent_id=parent_id or kb_id,
        file_name=filename,
        file_ext=suffix,
        content=content,
        content_type=content_type,
        principal=principal,
        inherit_parser_config=False,
        parser_id="qa",
        parser_config={"doc_type": "qa", "auto_questions": 0},
    )


async def create_qa_import_resources(
    db: AsyncSession,
    plan: file_service.UploadPlan,
    principal: Principal,
) -> QAImportResources:
    outcome = await file_service.persist_uploaded_content(db, plan, principal)
    return QAImportResources(
        document_id=outcome.document_id,
        file_id=outcome.file_id,
    )


__all__ = [
    "QAImportResources",
    "create_qa_import_resources",
    "dispatch_qa_import",
    "prepare_qa_import_resources",
    "validate_qa_upload",
]
