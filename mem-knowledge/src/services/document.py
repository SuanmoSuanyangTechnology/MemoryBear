"""Knowledge document behavior migrated from the legacy async controller."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import TYPE_CHECKING, Any, Literal, cast

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.dependencies import Principal
from ..api.query_validation import parse_uuid_filter
from ..api.schemas.document import DocumentCreate, DocumentUpdate
from ..error_mapping import map_storage_error
from ..errors import KnowledgeError
from ..models.owned import (
    FILE_ROLE_DERIVED_IMAGE,
    Document,
    File,
    Knowledge,
    KnowledgeMetadataBinding,
)
from ..parser_configuration import to_runtime
from ..parser_configuration.legacy import (
    document_configuration_from_legacy,
    reflect_first_document,
    validate_document_chunk_mode,
)
from ..rag.knowledge_graph import (
    GraphPipeline,
    GraphPipelineConfigError,
    is_graph_enabled,
    resolve_graph_pipeline,
)
from ..rag.parser_config import normalize_document_parser_config
from ..repositories import document as document_repository
from ..repositories.parser_configuration import (
    AsyncParserConfigurationRepository,
    document_configuration,
    knowledge_configuration,
)
from ..repositories.wiki import read as wiki_read
from ..tasks.dispatch import TaskDispatcher, TaskNotPublishedError
from ..tasks.state import PARSE_CLAIM_PREFIX
from ..utils.datetime_utils import to_iso_z, utcnow, utcnow_naive
from . import file as file_service
from . import knowledge as knowledge_service
from .document_mutation_guard import (
    DocumentMutationLeaseLost,
    async_document_mutation_guard,
)
from .knowledge_file_storage import KnowledgeFileStorage
from .legacy_projection import document_to_data as document_to_data
from .qa_export import collection_name_for_knowledge
from .write_validation import validate_write_fields

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ..runtime import ProcessRuntime

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

_COMPARE_AND_RECORD = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    redis.call('set', KEYS[1], ARGV[2], 'EX', ARGV[3])
    return 1
end
return 0
""".strip()


def _not_found(code: str = "KB_DOCUMENT_NOT_FOUND") -> KnowledgeError:
    return KnowledgeError.from_code(code)


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
    include_wiki_count: bool = False,
    project: Callable[[Document], dict[str, Any]] = document_to_data,
) -> tuple[int, list[dict[str, Any]]]:
    if await knowledge_service.get_knowledge(db, kb_id, principal) is None:
        raise KnowledgeError.from_code("KB_KNOWLEDGE_NOT_FOUND")

    filters = [Document.kb_id == kb_id]
    if parent_id is not None:
        await file_service.require_parent_folder(db, kb_id, parent_id, principal)
        result = await db.execute(
            select(File.id).where(File.parent_id == parent_id, File.kb_id == kb_id)
        )
        filters.append(Document.file_id.in_(list(result.scalars().all())))
    if keywords:
        filters.append(Document.file_name.ilike(f"%{keywords}%"))
    parsed_ids = parse_uuid_filter(document_ids, field="document_ids")
    if parsed_ids:
        filters.append(Document.id.in_(parsed_ids))

    total, documents = await document_repository.get_documents_paginated_async(
        db,
        filters,
        page,
        pagesize,
        orderby,
        desc,
    )
    items = [project(document) for document in documents]
    if include_wiki_count and documents:
        counts = await wiki_read.count_pages_by_document(
            db,
            {
                "kb_id": kb_id,
                "tenant_id": principal.tenant_id,
                "workspace_id": principal.workspace_id,
            },
            [document.id for document in documents],
        )
        for document, item in zip(documents, items, strict=True):
            item["wiki_count"] = counts.get(document.id, 0)
    return total, items


async def create_document(
    db: AsyncSession,
    create_data: DocumentCreate,
    principal: Principal,
) -> Document:
    if await knowledge_service.get_knowledge(db, create_data.kb_id, principal) is None:
        raise KnowledgeError.from_code("KB_KNOWLEDGE_NOT_FOUND")
    if (
        await file_service.get_file(
            db,
            create_data.file_id,
            principal,
            create_data.kb_id,
        )
        is None
    ):
        raise KnowledgeError.from_code("KB_FILE_NOT_FOUND")
    try:
        parser_config = normalize_document_parser_config(create_data.parser_config)
    except (ValueError, GraphPipelineConfigError) as exc:
        raise KnowledgeError.from_code("KB_DOCUMENT_PARSER_CONFIG_INVALID") from exc
    validate_write_fields(create_data.model_dump(), integers=("file_size",))
    payload = create_data.model_copy(
        update={"created_by": principal.actor_id, "parser_config": parser_config}
    )
    return await document_repository.create_document_async(db, payload)


@dataclass(frozen=True)
class DocumentUpdatePlan:
    document_id: uuid.UUID
    knowledge_id: uuid.UUID
    update_fields: dict[str, Any]
    status_changed: bool
    existing_chunk_method: str | None = None


async def prepare_document_update(
    db: AsyncSession,
    document_id: uuid.UUID,
    update_data: DocumentUpdate,
    principal: Principal,
) -> DocumentUpdatePlan:
    document = await get_document(db, document_id, principal)
    if document is None:
        raise KnowledgeError.from_code("KB_DOCUMENT_NOT_FOUND")
    knowledge = await knowledge_service.get_knowledge(db, document.kb_id, principal)
    if knowledge is None:
        raise KnowledgeError.from_code("KB_KNOWLEDGE_NOT_FOUND")

    update_fields = update_data.model_dump(exclude_unset=True)
    file_id = update_fields.get("file_id")
    if file_id is not None:
        if await file_service.get_file(db, file_id, principal, document.kb_id) is None:
            raise KnowledgeError.from_code("KB_FILE_NOT_FOUND")

    current_knowledge = knowledge_configuration(knowledge)
    existing_chunk_method = None
    if "parser_config" in update_fields:
        parser_config = update_fields["parser_config"]
        if not isinstance(parser_config, dict):
            raise KnowledgeError.from_code(
                "KB_PARSER_CONFIG_OBJECT_REQUIRED",
            )
        try:
            parser_config = normalize_document_parser_config(parser_config)
        except (ValueError, GraphPipelineConfigError) as exc:
            raise KnowledgeError.from_code("KB_DOCUMENT_PARSER_CONFIG_INVALID") from exc
        update_fields["parser_config"] = parser_config
        current_document = document_configuration(document)
        next_document = document_configuration_from_legacy(
            parser_config, current_knowledge, current=current_document
        )
        validate_document_chunk_mode(current_knowledge, next_document, current=current_document)
        if current_knowledge.chunk_method is not None and (
            (current_knowledge.chunk_method == "parent_child")
            != (current_document.chunk_method == "parent_child")
        ):
            existing_chunk_method = current_document.chunk_method

    validate_write_fields(
        update_fields,
        integers=("file_size", "chunk_num", "run", "status"),
        non_nullable=(
            "file_id", "file_name", "file_ext", "file_size", "file_meta", "parser_id",
            "chunk_num", "progress", "progress_msg", "process_begin_at", "process_duration",
            "run", "status",
        ),
        finite=("progress", "process_duration"),
    )
    status_changed = (
        "status" in update_fields and update_fields["status"] != document.status
    )
    return DocumentUpdatePlan(
        document_id=document.id,
        knowledge_id=document.kb_id,
        update_fields=update_fields,
        status_changed=status_changed,
        existing_chunk_method=existing_chunk_method,
    )


async def apply_document_update(
    db: AsyncSession,
    plan: DocumentUpdatePlan,
    principal: Principal,
) -> tuple[Document, list[str], dict | None, dict[str, Any]]:
    document = await get_document(db, plan.document_id, principal, plan.knowledge_id)
    if document is None:
        raise KnowledgeError.from_code("KB_DOCUMENT_NOT_FOUND")
    knowledge = await knowledge_service.get_knowledge(db, plan.knowledge_id, principal)
    if knowledge is None:
        raise KnowledgeError.from_code("KB_KNOWLEDGE_NOT_FOUND")

    knowledge = (await db.execute(
        select(Knowledge).where(Knowledge.id == knowledge.id)
        .with_for_update().execution_options(populate_existing=True)
    )).scalar_one()
    await db.refresh(document, with_for_update=True)
    parser_config = plan.update_fields.get("parser_config")
    if parser_config is not None:
        current_knowledge = knowledge_configuration(knowledge)
        current_document = document_configuration(document)
        next_document = document_configuration_from_legacy(
            parser_config, current_knowledge, current=current_document
        )
        # Only preserve a mismatch that existed when this request was prepared;
        # a concurrent first-document update must still recheck the KB's mode.
        validate_document_chunk_mode(
            current_knowledge, next_document,
            current=(
                current_document
                if plan.existing_chunk_method == current_document.chunk_method
                else None
            ),
        )
        repository = AsyncParserConfigurationRepository(db)
        if current_knowledge.chunk_method is None:
            await repository.write_knowledge(knowledge, lambda _: reflect_first_document(
                current_knowledge, parser_config
            ))
        await repository.write_document(plan.knowledge_id, document, lambda _: next_document)
    graph_config = to_runtime(knowledge_configuration(knowledge))
    for field, value in plan.update_fields.items():
        if field != "parser_config" and hasattr(document, field):
            setattr(document, field, value)
    await db.flush()

    wiki_pages = []
    wiki_delivery = None
    if plan.update_fields.get("run") == 2:
        from ..repositories.wiki.state import cancel

        wiki_pages = await db.run_sync(lambda session: cancel(session, knowledge.id, document.id))
    document.updated_at = utcnow_naive()
    try:
        await db.commit()
        await db.refresh(document)
        return document, wiki_pages, wiki_delivery, graph_config
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
    refresh_result = await client.indices.refresh(index=index)
    if refresh_result.get("_shards", {}).get("failed", 0):
        raise KnowledgeError.from_code("KB_SEARCH_UNAVAILABLE", http_status_override=500)
    result = await client.delete_by_query(
        index=index,
        query={"term": {"metadata.document_id": str(document_id)}},
        refresh=True,
        conflicts="abort",
        wait_for_completion=True,
    )
    failures = result.get("failures") or []
    if result.get("timed_out") or failures or result.get("_shards", {}).get("failed", 0):
        raise KnowledgeError.from_code(
            "KB_SEARCH_UNAVAILABLE",
            http_status_override=503 if result.get("timed_out") else 500,
        )
    deleted = int(result.get("deleted", 0))
    logger.info(
        "Document search data deleted: knowledge=%s document=%s deleted=%s",
        knowledge_id,
        document_id,
        deleted,
    )
    return deleted


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
    qa_knowledge_id: uuid.UUID | None = None


@dataclass
class ParseDispatchResult:
    """Publication facts, separate from the legacy task-id placeholder contract."""

    task_id: str | None = None
    dispatched: bool = False
    claim_value: str | None = None
    publication: Literal["not_attempted", "not_published", "published", "unknown"] = "not_attempted"
    error: Exception | None = dataclass_field(default=None, repr=False)

    @property
    def legacy_task_id(self) -> str:
        if self.claim_value and self.claim_value.startswith(PARSE_CLAIM_PREFIX):
            return "CLAIMED"
        return self.task_id or self.claim_value or "unknown"


def _real_parse_task_id(value: str) -> str | None:
    return (value if value and value not in {"CLAIMED", "unknown"}
            and not value.startswith(PARSE_CLAIM_PREFIX) else None)


async def _release_parse_claim(
    redis: Any, task_key: str, claim_value: str = "CLAIMED",
) -> None:
    if hasattr(redis, "eval"):
        await redis.eval(_COMPARE_AND_DELETE, 1, task_key, claim_value)
        return
    if await redis.get(task_key) == claim_value:
        await redis.delete(task_key)


async def _persist_parse_dispatch_state(
    runtime: ProcessRuntime,
    document_id: uuid.UUID,
    *,
    state: str,
) -> None:
    async with runtime.database.async_session() as db:
        document = await db.get(Document, document_id)
        if document is None:
            raise ValueError(f"Document {document_id} not found while updating dispatch state")
        timestamp = to_iso_z(utcnow())
        if state == "queued":
            document.progress = 0.0
            document.progress_msg = f"{timestamp} Queued.\n"
            document.process_duration = 0.0
            document.run = 0
        elif state == "failed":
            document.progress = -1.0
            document.progress_msg = f"{timestamp} Task dispatch failed.\n"
            document.run = 0
        else:
            raise ValueError(f"Unsupported parse dispatch state: {state}")
        await db.commit()


async def claim_and_dispatch_parse(
    runtime: ProcessRuntime,
    dispatcher: TaskDispatcher,
    snapshot: ParseDocumentSnapshot,
    *,
    preserve_unknown: bool = False,
) -> ParseDispatchResult:
    """V2 observes failures and retains uncertain claims; legacy callers still raise."""
    result = await dispatch_parse_result(
        runtime, dispatcher, snapshot, preserve_unknown=preserve_unknown,
    )
    if result.error is not None and not preserve_unknown:
        raise result.error
    return result


async def dispatch_parse_result(
    runtime: ProcessRuntime,
    dispatcher: TaskDispatcher,
    snapshot: ParseDocumentSnapshot,
    *,
    preserve_unknown: bool = False,
) -> ParseDispatchResult:
    """Observe dispatch without changing the legacy claim/failure side effects."""
    result = ParseDispatchResult()
    try:
        redis = await runtime.redis.client()
        task_key = PARSE_TASK_KEY.format(doc_id=snapshot.document_id)
        proposed_task_id = str(uuid.uuid4()) if preserve_unknown else None
        claim_value = PARSE_CLAIM_PREFIX + proposed_task_id if proposed_task_id else "CLAIMED"
        claimed = await redis.set(task_key, claim_value, ex=PARSE_TASK_TTL, nx=True)
        if not claimed:
            result.claim_value = str(await redis.get(task_key) or "unknown")
            result.task_id = _real_parse_task_id(result.claim_value)
            return result
        result.claim_value = claim_value
        try:
            await _persist_parse_dispatch_state(
                runtime,
                snapshot.document_id,
                state="queued",
            )
            result.publication = "unknown"
            try:
                publish_options: dict[str, Any] = (
                    {"connect_before_publish": True, "task_id": proposed_task_id}
                    if preserve_unknown else {}
                )
                if snapshot.qa_knowledge_id is not None:
                    task_id = await dispatcher.send(
                        "app.core.rag.tasks.import_qa_chunks",
                        args=[str(snapshot.qa_knowledge_id), str(snapshot.document_id),
                              snapshot.file_name],
                        kwargs={"file_key": snapshot.file_key, "clear_parse_task": True},
                        queue="qa_import",
                        **publish_options,
                    )
                else:
                    task_id = await dispatcher.send(
                        PARSE_TASK_NAME,
                        args=[snapshot.file_key, snapshot.document_id, snapshot.file_name],
                        queue="document_tasks",
                        **publish_options,
                    )
            except TaskNotPublishedError:
                result.publication = "not_published"
                raise
        except Exception:
            if preserve_unknown and result.publication == "unknown":
                # The broker may have accepted the message. Keep the claim until
                # the worker clears it or its lease expires; never auto-resubmit.
                raise
            await _release_parse_claim(redis, task_key, claim_value)
            try:
                await _persist_parse_dispatch_state(
                    runtime,
                    snapshot.document_id,
                    state="failed",
                )
            except Exception as state_exc:  # noqa: BLE001 - preserve dispatch failure.
                logger.warning(
                    "Failed to persist parse dispatch failure: document=%s error_type=%s",
                    snapshot.document_id,
                    type(state_exc).__name__,
                )
            raise
        result.task_id = _real_parse_task_id(task_id)
        result.claim_value = task_id
        result.dispatched = True
        result.publication = "published"
        if preserve_unknown and hasattr(redis, "eval"):
            # A fast worker may already have finished, or a newer claim may exist.
            await cast(Any, redis).eval(
                _COMPARE_AND_RECORD, 1, task_key, claim_value, task_id, PARSE_TASK_TTL,
            )
        elif not preserve_unknown or await redis.get(task_key) == claim_value:
            await redis.set(task_key, task_id, ex=PARSE_TASK_TTL)
    except Exception as exc:
        result.error = exc
    return result


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
        raise KnowledgeError.from_code("KB_DOCUMENT_NOT_FOUND")
    knowledge = await knowledge_service.get_knowledge(db, document.kb_id, principal)
    if knowledge is None:
        raise KnowledgeError.from_code("KB_KNOWLEDGE_NOT_FOUND")

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
        parser_config=to_runtime(knowledge_configuration(knowledge)),
        storage_keys=tuple(file.file_key for file in files if file.file_key),
        file_ids=tuple(file.id for file in files),
    )


async def delete_document_records(
    db: AsyncSession,
    snapshot: DocumentDeletionSnapshot,
) -> list[str]:
    try:
        from ..repositories.wiki.state import withdraw

        wiki_pages = await db.run_sync(lambda session: withdraw(
            session, snapshot.knowledge_id, snapshot.document_id, hard_delete=True
        ))
        if snapshot.file_ids:
            await db.execute(delete(File).where(File.id.in_(snapshot.file_ids)))
        await db.execute(
            delete(KnowledgeMetadataBinding).where(
                KnowledgeMetadataBinding.document_id == snapshot.document_id
            )
        )
        await db.execute(delete(Document).where(Document.id == snapshot.document_id))
        await db.commit()
        return wiki_pages
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
    delete_records: Callable[[DocumentDeletionSnapshot], Awaitable[None]],
    refresh_assets: Callable[[], Awaitable[DocumentDeletionSnapshot]],
) -> None:
    task_key = PARSE_TASK_KEY.format(doc_id=snapshot.document_id)
    task_id = await redis.get(task_key)
    if task_id and task_id != "CLAIMED":
        await dispatcher.revoke(str(task_id))
    await redis.set(
        PARSE_CANCEL_KEY.format(doc_id=snapshot.document_id),
        "1",
        # Cover the existing parse-task lifetime while storage cleanup runs.
        ex=PARSE_TASK_TTL,
    )
    storage_cleaned = False
    records_deleted = False
    try:
        await redis.delete(task_key)

        await dispatch_document_graph_sync(
            dispatcher,
            snapshot.knowledge_id,
            snapshot.document_id,
            snapshot.parser_config,
            dispatch_legacy=False,
            document_deleted=True,
        )
        async with async_document_mutation_guard(redis, snapshot.document_id) as guard:
            snapshot = await refresh_assets()
            await guard.ensure_owned()
            await delete_search()
            await guard.ensure_owned()
            storage_failure: KnowledgeError | None = None
            for storage_key in snapshot.storage_keys:
                try:
                    await storage.delete(storage_key)
                except Exception as exc:
                    mapped = map_storage_error(exc)
                    if storage_failure is None or mapped.http_status_override == 500:
                        storage_failure = mapped
                    logger.warning(
                        "删除文档存储对象失败 document_id=%s key=%s "
                        "error_type=%s stage=delete_storage",
                        snapshot.document_id, storage_key, type(exc).__name__, exc_info=True,
                    )
            if storage_failure is not None:
                raise KnowledgeError.from_code(
                    "KB_STORAGE_UNAVAILABLE",
                    http_status_override=storage_failure.http_status_override,
                ) from storage_failure
            storage_cleaned = True
            # Retain the refreshed asset keys until storage cleanup succeeds,
            # while keeping ES mutations fenced until the database commit.
            await guard.ensure_owned()
            await delete_records(snapshot)
            records_deleted = True
            await guard.ensure_owned()
    except (TimeoutError, DocumentMutationLeaseLost) as exc:
        if storage_cleaned:
            # Storage deletion is irreversible. Once it succeeded, reacquire
            # the lease and finish the matching durable deletion if possible.
            try:
                async with async_document_mutation_guard(redis, snapshot.document_id) as guard:
                    await guard.ensure_owned()
                    await delete_search()
                    await guard.ensure_owned()
                    if not records_deleted:
                        await delete_records(snapshot)
                    await guard.ensure_owned()
                return
            except Exception as recovery_exc:
                logger.warning(
                    "Document deletion recovery failed: document=%s error_type=%s",
                    snapshot.document_id,
                    type(recovery_exc).__name__,
                )
                raise KnowledgeError.from_code(
                    "KB_SEARCH_UNAVAILABLE",
                    http_status_override=503 if isinstance(
                        recovery_exc, (TimeoutError, DocumentMutationLeaseLost)
                    ) else 500,
                ) from recovery_exc
        raise KnowledgeError.from_code("KB_SEARCH_UNAVAILABLE", http_status_override=503) from exc
    finally:
        # Retain the normal short grace period after success or failure.
        try:
            await redis.expire(
                PARSE_CANCEL_KEY.format(doc_id=snapshot.document_id), PARSE_CANCEL_TTL
            )
        except Exception as exc:
            logger.warning(
                "Failed to shorten document cancellation expiry: document=%s error_type=%s",
                snapshot.document_id,
                type(exc).__name__,
            )


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
