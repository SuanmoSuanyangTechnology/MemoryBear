"""Shared lock order and document progress settlement for Wiki transactions."""

from __future__ import annotations

import uuid
from collections import Counter

from sqlalchemy import select

from ...models.owned import Document, Knowledge, WikiDocumentPageTask, WikiDocumentState
from ...models.references import Workspace
from ...rag.wiki.config import WikiError, WikiSuperseded
from ...utils.datetime_utils import to_iso_z, utcnow_naive


def as_uuid(value) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def lock_knowledge(session, kb_id, expected_scope=None, *, allow_deleted=False):
    """All Wiki writers lock KB, then documents/states, then pages in ID order."""
    knowledge = session.scalar(
        select(Knowledge)
        .where(Knowledge.id == as_uuid(kb_id))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if knowledge is None or (knowledge.status == 2 and not allow_deleted) or knowledge.is_folder:
        raise WikiSuperseded("WIKI_KNOWLEDGE_UNAVAILABLE", retryable=False)
    workspace = session.get(Workspace, knowledge.workspace_id)
    if workspace is None:
        raise WikiError("WIKI_WORKSPACE_UNAVAILABLE", retryable=False)
    scope = {"kb_id": knowledge.id, "workspace_id": workspace.id, "tenant_id": workspace.tenant_id}
    if expected_scope and any(scope[k] != as_uuid(expected_scope[k]) for k in scope):
        raise WikiError("WIKI_SCOPE_MISMATCH", retryable=False)
    return knowledge, scope


def lock_document(session, kb_id, document_id):
    document = session.scalar(
        select(Document)
        .where(Document.id == as_uuid(document_id), Document.kb_id == as_uuid(kb_id))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if document is None:
        raise WikiSuperseded("WIKI_DOCUMENT_UNAVAILABLE", retryable=False)
    state = session.scalar(
        select(WikiDocumentState)
        .where(WikiDocumentState.document_id == document.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return document, state


def lock_documents(session, kb_id, document_ids):
    ids = {as_uuid(value) for value in document_ids}
    if not ids:
        return {}, {}
    documents = {
        row.id: row
        for row in session.scalars(
            select(Document)
            .where(Document.kb_id == as_uuid(kb_id), Document.id.in_(ids))
            .order_by(Document.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    }
    if set(documents) != ids:
        raise WikiSuperseded("WIKI_DOCUMENT_UNAVAILABLE", retryable=False)
    states = {
        row.document_id: row
        for row in session.scalars(
            select(WikiDocumentState)
            .where(WikiDocumentState.document_id.in_(ids))
            .order_by(WikiDocumentState.document_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    }
    return documents, {key: states.get(key) for key in ids}


def require_round(document, state, version, *, allow_inactive=False):
    if (
        state is None
        or state.requested_version != version
        or document.run == 2
        or state.status in {"cancelled", "failed"}
        or (not allow_inactive and document.status != 1)
    ):
        raise WikiSuperseded("WIKI_ROUND_SUPERSEDED", retryable=False)


def append_log(document, message):
    document.progress_msg = (
        document.progress_msg or ""
    ) + f"{to_iso_z(utcnow_naive())} {message}\n"
    document.updated_at = utcnow_naive()


def current_tasks(session, state):
    return list(
        session.scalars(
            select(WikiDocumentPageTask)
            .where(
                WikiDocumentPageTask.document_id == state.document_id,
                WikiDocumentPageTask.requested_version == state.requested_version,
            )
            .order_by(WikiDocumentPageTask.page_id)
        )
    )


def settle_progress(session, document, state):
    """The caller holds the document lock; results, progress and log commit together."""
    if document.run == 2 or state.status == "cancelled":
        return
    started = getattr(document, "process_begin_at", None)
    if started is not None:
        document.process_duration = max(0.0, (utcnow_naive() - started).total_seconds())
    tasks = current_tasks(session, state)
    counts = Counter(task.status for task in tasks)
    finished = counts["succeeded"] + counts["failed"]
    total = len(tasks)
    if state.status == "failed" and state.mapped_version != state.requested_version:
        document.run, document.progress = 0, -1.0
        return
    if not state.plan_ready or state.mapped_version != state.requested_version:
        return
    previous = (state.status, document.run, document.progress)
    if finished == total:
        has_upserts = any(getattr(task, "operation", "upsert") == "upsert" for task in tasks)
        success_status = (
            "ready"
            if has_upserts or getattr(state, "operation", "upsert") == "retract"
            else "skipped"
        )
        state.status = "failed" if counts["failed"] else success_status
        state.stage = "complete"
        document.run = 0
        document.progress = -1.0 if counts["failed"] else 1.0
    else:
        base = 0.8 if state.processing_mode == "source" else 0.2
        document.progress = max(state.last_progress, base + (0.99 - base) * finished / total)
        document.run = 1
        state.status, state.stage = "running", "reducing"
    state.last_progress = document.progress
    if previous != (state.status, document.run, document.progress):
        append_log(
            document,
            f"Wiki pages: finished={finished}/{total}, "
            f"succeeded={counts['succeeded']}, failed={counts['failed']}; "
            f"status={state.status}.",
        )


def state_delivery(state):
    return {
        "kb_id": str(state.kb_id),
        "document_id": str(state.document_id),
        "requested_version": state.requested_version,
        "task_id": state.task_id,
        "operation": state.operation,
    }
