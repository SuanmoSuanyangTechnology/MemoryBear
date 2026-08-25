"""Knowledge document behavior migrated from the legacy async controller."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from ..api.dependencies import Principal
from ..api.schemas.document import Document as DocumentSchema
from ..api.schemas.document import DocumentCreate, DocumentUpdate
from ..errors import KnowledgeError
from ..models.owned import (
    FILE_ROLE_DERIVED_IMAGE,
    Document,
    File,
    KnowledgeMetadataBinding,
)
from ..rag.knowledge_graph import GraphPipeline, is_graph_enabled, resolve_graph_pipeline
from ..repositories import document as document_repository
from ..tasks.dispatch import TaskDispatcher
from ..utils.datetime_utils import utcnow_naive
from . import file as file_service
from . import knowledge as knowledge_service
from .knowledge_file_storage import KnowledgeFileStorage
from .qa_export import collection_name_for_knowledge

logger = logging.getLogger(__name__)

PARSE_TASK_KEY = "doc:{doc_id}:parse_task"
PARSE_CANCEL_KEY = "doc:{doc_id}:parse_cancel"
PARSE_TASK_TTL = 7200
PARSE_CANCEL_TTL = 60
PARSE_TASK_NAME = "app.core.rag.tasks.parse_document"

_COMPARE_AND_DELETE = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
""".strip()


def _not_found(message: str = "Document resource not found") -> KnowledgeError:
    return KnowledgeError.from_code("KB_RESOURCE_NOT_FOUND", message)


def document_to_data(document: Document) -> dict[str, Any]:
    return DocumentSchema.model_validate(document).model_dump(mode="json")


async def get_document(
    db: AsyncSession,
    document_id: uuid.UUID,
    principal: Principal,
    kb_id: uuid.UUID | None = None,
) -> Document | None:
    return await document_repository.get_document_by_id_in_workspace_async(
        db,
        document_id,
        principal.workspace_id,
        kb_id,
    )


async def list_documents(
    db: AsyncSession,
    kb_id: uuid.UUID,
    principal: Principal,
    *,
    parent_id: uuid.UUID | None,
    page: int,
    pagesize: int,
    orderby: str | None,
    desc: bool,
    keywords: str | None,
    document_ids: str | None,
) -> tuple[int, list[dict[str, Any]]]:
    if await knowledge_service.get_knowledge(db, kb_id, principal) is None:
        raise _not_found("Knowledge resource not found")

    filters = [Document.kb_id == kb_id, Document.status == 1]
    if parent_id is not None:
        await file_service.require_parent_folder(db, kb_id, parent_id, principal)
        result = await db.execute(
            select(File.id).where(File.parent_id == parent_id, File.kb_id == kb_id)
        )
        filters.append(Document.file_id.in_(list(result.scalars().all())))
    if keywords:
        filters.append(Document.file_name.ilike(f"%{keywords}%"))
    if document_ids:
        filters.append(Document.id.in_(document_ids.split(",")))

    total, documents = await document_repository.get_documents_paginated_async(
        db,
        filters,
        page,
        pagesize,
        orderby,
        desc,
    )
    return total, [document_to_data(document) for document in documents]


async def create_document(
    db: AsyncSession,
    create_data: DocumentCreate,
    principal: Principal,
) -> Document:
    if await knowledge_service.get_knowledge(db, create_data.kb_id, principal) is None:
        raise _not_found("Knowledge resource not found")
    if (
        await file_service.get_file(
            db,
            create_data.file_id,
            principal,
            create_data.kb_id,
        )
        is None
    ):
        raise _not_found("File resource not found")
    payload = create_data.model_copy(update={"created_by": principal.actor_id})
    return await document_repository.create_document_async(db, payload)


@dataclass(frozen=True)
class DocumentUpdatePlan:
    document_id: uuid.UUID
    knowledge_id: uuid.UUID
    update_fields: dict[str, Any]
    status_changed: bool
    graph_parser_config: dict[str, Any]


def _uses_parent_child_mode(parser_config: dict[str, Any]) -> bool:
    if "parent_child_mode" in parser_config:
        return bool(parser_config["parent_child_mode"])
    return parser_config.get("parent_chunk_mode") in {"paragraph", "full-doc"}


async def prepare_document_update(
    db: AsyncSession,
    document_id: uuid.UUID,
    update_data: DocumentUpdate,
    principal: Principal,
) -> DocumentUpdatePlan:
    document = await get_document(db, document_id, principal)
    if document is None:
        raise _not_found()
    knowledge = await knowledge_service.get_knowledge(db, document.kb_id, principal)
    if knowledge is None:
        raise _not_found("Knowledge resource not found")

    update_fields = update_data.model_dump(exclude_unset=True)
    file_id = update_fields.get("file_id")
    if file_id is not None:
        if await file_service.get_file(db, file_id, principal, document.kb_id) is None:
            raise _not_found("File resource not found")

    graph_parser_config = dict(knowledge.parser_config or {})
    if "parser_config" in update_fields:
        parser_config = update_fields["parser_config"]
        if not isinstance(parser_config, dict):
            raise KnowledgeError.from_code(
                "KB_VALIDATION_ERROR",
                "parser_config must be an object",
            )
        parent_child_mode = _uses_parent_child_mode(parser_config)
        chunk_mode = knowledge.chunk_mode
        if (chunk_mode == 1 and parent_child_mode) or (
            chunk_mode == 2 and not parent_child_mode
        ):
            raise KnowledgeError.from_code(
                "KB_VALIDATION_ERROR",
                "禁止变更分块模式",
            )
        if chunk_mode == 0:
            graph_parser_config.update(parser_config)

    status_changed = (
        "status" in update_fields and update_fields["status"] != document.status
    )
    return DocumentUpdatePlan(
        document_id=document.id,
        knowledge_id=document.kb_id,
        update_fields=update_fields,
        status_changed=status_changed,
        graph_parser_config=graph_parser_config,
    )


async def apply_document_update(
    db: AsyncSession,
    plan: DocumentUpdatePlan,
    principal: Principal,
) -> Document:
    document = await get_document(db, plan.document_id, principal, plan.knowledge_id)
    if document is None:
        raise _not_found()
    knowledge = await knowledge_service.get_knowledge(db, plan.knowledge_id, principal)
    if knowledge is None:
        raise _not_found("Knowledge resource not found")

    parser_config = plan.update_fields.get("parser_config")
    if parser_config is not None and knowledge.chunk_mode == 0:
        merged_config = dict(knowledge.parser_config or {})
        merged_config.update(parser_config)
        knowledge.parser_config = merged_config
        flag_modified(knowledge, "parser_config")
    for field, value in plan.update_fields.items():
        if hasattr(document, field):
            setattr(document, field, value)
    document.updated_at = utcnow_naive()
    try:
        await db.commit()
        await db.refresh(document)
        return document
    except Exception:
        await db.rollback()
        raise


async def change_document_status(
    client: Any,
    knowledge_id: uuid.UUID,
    document_id: uuid.UUID,
    status: int,
) -> int:
    result = await client.update_by_query(
        index=collection_name_for_knowledge(knowledge_id),
        body={
            "script": {
                "source": "ctx._source.metadata.status = params.new_status",
                "params": {"new_status": status},
            },
            "query": {"term": {"metadata.document_id": str(document_id)}},
        },
    )
    return int(result.get("updated", 0))


async def delete_document_search_data(
    client: Any,
    knowledge_id: uuid.UUID,
    document_id: uuid.UUID,
) -> int:
    index = collection_name_for_knowledge(knowledge_id)
    if not await client.indices.exists(index=index):
        return 0
    result = await client.delete_by_query(
        index=index,
        query={"term": {"metadata.document_id": str(document_id)}},
        refresh=False,
        conflicts="abort",
        wait_for_completion=True,
    )
    failures = result.get("failures") or []
    if failures:
        raise KnowledgeError.from_code(
            "KB_SEARCH_UNAVAILABLE",
            "Failed to delete document search data",
        )
    return int(result.get("deleted", 0))


async def dispatch_document_graph_sync(
    dispatcher: TaskDispatcher,
    knowledge_id: uuid.UUID,
    document_id: uuid.UUID,
    parser_config: dict[str, Any] | None,
    *,
    dispatch_legacy: bool = False,
    document_deleted: bool = False,
) -> str | None:
    if not is_graph_enabled(parser_config):
        return None
    pipeline = resolve_graph_pipeline(parser_config)
    if pipeline is GraphPipeline.LEGACY:
        logger.warning(
            "Legacy graph document sync removed; skipping: knowledge=%s document=%s",
            knowledge_id,
            document_id,
        )
        return None
    args: list[Any] = [str(knowledge_id), str(document_id)]
    if document_deleted:
        args.append(True)
    return await dispatcher.send(
        "app.core.rag.tasks.sync_evidence_graph_document",
        args=args,
        queue="graphrag_tasks",
    )


@dataclass(frozen=True)
class ParseDocumentSnapshot:
    document_id: uuid.UUID
    file_key: str
    file_name: str


@dataclass(frozen=True)
class ParseDispatchResult:
    task_id: str
    dispatched: bool


async def _release_parse_claim(redis: Any, task_key: str) -> None:
    if hasattr(redis, "eval"):
        await redis.eval(_COMPARE_AND_DELETE, 1, task_key, "CLAIMED")
        return
    if await redis.get(task_key) == "CLAIMED":
        await redis.delete(task_key)


async def claim_and_dispatch_parse(
    redis: Any,
    dispatcher: TaskDispatcher,
    snapshot: ParseDocumentSnapshot,
) -> ParseDispatchResult:
    task_key = PARSE_TASK_KEY.format(doc_id=snapshot.document_id)
    claimed = await redis.set(task_key, "CLAIMED", ex=PARSE_TASK_TTL, nx=True)
    if not claimed:
        existing_task_id = await redis.get(task_key)
        return ParseDispatchResult(str(existing_task_id or "unknown"), False)
    try:
        task_id = await dispatcher.send(
            PARSE_TASK_NAME,
            args=[snapshot.file_key, snapshot.document_id, snapshot.file_name],
            queue="document_tasks",
        )
    except Exception:
        await _release_parse_claim(redis, task_key)
        raise
    await redis.set(task_key, task_id, ex=PARSE_TASK_TTL)
    return ParseDispatchResult(task_id, True)


@dataclass(frozen=True)
class DocumentDeletionSnapshot:
    document_id: uuid.UUID
    knowledge_id: uuid.UUID
    parser_config: dict[str, Any]
    storage_keys: tuple[str, ...]
    file_ids: tuple[uuid.UUID, ...] = ()


async def prepare_document_deletion(
    db: AsyncSession,
    document_id: uuid.UUID,
    principal: Principal,
) -> DocumentDeletionSnapshot:
    document = await get_document(db, document_id, principal)
    if document is None:
        raise _not_found()
    knowledge = await knowledge_service.get_knowledge(db, document.kb_id, principal)
    if knowledge is None:
        raise _not_found("Knowledge resource not found")

    file_result = await db.execute(
        select(File).where(File.id == document.file_id, File.kb_id == document.kb_id)
    )
    source_file = file_result.scalars().first()
    files = [source_file] if source_file is not None else []
    if source_file is not None and source_file.file_ext == "folder":
        child_result = await db.execute(
            select(File).where(
                File.parent_id == source_file.id,
                File.kb_id == document.kb_id,
            )
        )
        files.extend(child_result.scalars().all())
    derived_result = await db.execute(
        select(File).where(
            File.source_document_id == document.id,
            File.file_role == FILE_ROLE_DERIVED_IMAGE,
        )
    )
    files.extend(derived_result.scalars().all())
    return DocumentDeletionSnapshot(
        document_id=document.id,
        knowledge_id=document.kb_id,
        parser_config=dict(knowledge.parser_config or {}),
        storage_keys=tuple(file.file_key for file in files if file.file_key),
        file_ids=tuple(file.id for file in files),
    )


async def delete_document_records(
    db: AsyncSession,
    snapshot: DocumentDeletionSnapshot,
) -> None:
    try:
        if snapshot.file_ids:
            await db.execute(delete(File).where(File.id.in_(snapshot.file_ids)))
        await db.execute(
            delete(KnowledgeMetadataBinding).where(
                KnowledgeMetadataBinding.document_id == snapshot.document_id
            )
        )
        await db.execute(delete(Document).where(Document.id == snapshot.document_id))
        await db.commit()
    except Exception:
        await db.rollback()
        raise


async def delete_document_resources(
    snapshot: DocumentDeletionSnapshot,
    *,
    redis: Any,
    dispatcher: TaskDispatcher,
    storage: KnowledgeFileStorage,
    delete_search: Callable[[], Awaitable[Any]],
    delete_records: Callable[[], Awaitable[None]],
) -> None:
    task_key = PARSE_TASK_KEY.format(doc_id=snapshot.document_id)
    task_id = await redis.get(task_key)
    if task_id and task_id != "CLAIMED":
        await dispatcher.revoke(str(task_id))
    await redis.set(
        PARSE_CANCEL_KEY.format(doc_id=snapshot.document_id),
        "1",
        ex=PARSE_CANCEL_TTL,
    )
    await redis.delete(task_key)

    await dispatch_document_graph_sync(
        dispatcher,
        snapshot.knowledge_id,
        snapshot.document_id,
        snapshot.parser_config,
        dispatch_legacy=False,
        document_deleted=True,
    )
    await delete_search()
    for storage_key in snapshot.storage_keys:
        try:
            await storage.delete(storage_key)
        except Exception:
            logger.warning(
                "Failed to delete document storage object: key=%s",
                storage_key,
            )
    await delete_records()


__all__ = [
    "DocumentDeletionSnapshot",
    "DocumentUpdatePlan",
    "PARSE_CANCEL_KEY",
    "PARSE_CANCEL_TTL",
    "PARSE_TASK_KEY",
    "PARSE_TASK_TTL",
    "ParseDispatchResult",
    "ParseDocumentSnapshot",
    "apply_document_update",
    "change_document_status",
    "claim_and_dispatch_parse",
    "create_document",
    "delete_document_records",
    "delete_document_resources",
    "delete_document_search_data",
    "dispatch_document_graph_sync",
    "document_to_data",
    "get_document",
    "list_documents",
    "prepare_document_deletion",
    "prepare_document_update",
]
