"""Persist retry budgets and terminal failures without overwriting newer work."""

from sqlalchemy import select

from ...models.owned import WikiDocumentPageTask, WikiPage
from ...rag.wiki.config import TERMINAL_TASK_STATUSES
from ...utils.datetime_utils import utcnow_naive
from .common import (
    append_log,
    as_uuid,
    current_tasks,
    lock_document,
    lock_documents,
    lock_knowledge,
    settle_progress,
)


def _attempt(target, error, settings, *, deferred):
    field = "defer_count" if deferred else "failure_attempt"
    count = getattr(target, field) + 1
    setattr(target, field, count)
    if deferred and hasattr(target, "defer_started_at"):
        now = utcnow_naive()
        if target.defer_started_at is None:
            target.defer_started_at = now
        elapsed = (now - target.defer_started_at).total_seconds()
        return error.retryable and elapsed < settings.kb_wiki_wait_timeout_seconds
    limit = settings.kb_wiki_defer_attempts if deferred else settings.kb_wiki_failure_attempts
    return error.retryable and count < limit


def map_failure(session, delivery, error, settings, *, deferred=False):
    lock_knowledge(session, delivery["kb_id"])
    document, state = lock_document(session, delivery["kb_id"], delivery["document_id"])
    if (
        not state
        or state.requested_version != delivery["requested_version"]
        or state.task_id != delivery["task_id"]
        or state.status in {"ready", "cancelled", "skipped", "failed"}
        or document.run == 2
    ):
        return False
    retry = _attempt(state, error, settings, deferred=deferred)
    state.error_code, state.error_stage = error.code, state.stage
    state.status = "retry_wait" if retry else "failed"
    append_log(document, f"Wiki {state.stage}: {error.code}; retry={str(retry).lower()}.")
    if not retry:
        failed_pages = set()
        for task in current_tasks(session, state):
            if task.status not in TERMINAL_TASK_STATUSES:
                task.status, task.error_code, task.error_stage = "failed", error.code, state.stage
                failed_pages.add(task.page_id)
        document.run, document.progress = 0, -1.0
        session.flush()
        for page_id in sorted(failed_pages, key=str):
            pending = session.scalar(
                select(WikiDocumentPageTask.subtask_id)
                .where(
                    WikiDocumentPageTask.page_id == page_id,
                    WikiDocumentPageTask.status.not_in(TERMINAL_TASK_STATUSES),
                )
                .limit(1)
            )
            if pending is None:
                page = session.scalar(
                    select(WikiPage).where(WikiPage.id == page_id).with_for_update()
                )
                if page and page.status != "ready":
                    page.status, page.error_code = "failed", error.code
                    page.row_version += 1
    return retry


def page_failure(session, kb_id, page_id, error, settings, *, deferred=False, snapshot=None):
    lock_knowledge(session, kb_id)
    page = session.scalar(
        select(WikiPage).where(WikiPage.id == as_uuid(page_id), WikiPage.kb_id == as_uuid(kb_id))
    )
    if page is None:
        return False
    if snapshot and page.row_version != snapshot["row_version"]:
        return True
    retry = None
    if deferred and page.error_code == error.code:
        pending = session.scalar(
            select(WikiDocumentPageTask.subtask_id)
            .where(
                WikiDocumentPageTask.kb_id == as_uuid(kb_id),
                WikiDocumentPageTask.page_id == as_uuid(page_id),
                WikiDocumentPageTask.status.not_in(TERMINAL_TASK_STATUSES),
            )
            .limit(1)
        )
        if pending is None and page.status != "cleanup_pending":
            return False
        retry = _attempt(page, error, settings, deferred=True)
        if retry:
            return True
    tasks = list(
        session.scalars(
            select(WikiDocumentPageTask)
            .where(
                WikiDocumentPageTask.kb_id == as_uuid(kb_id),
                WikiDocumentPageTask.page_id == as_uuid(page_id),
                WikiDocumentPageTask.status.not_in(TERMINAL_TASK_STATUSES),
            )
            .order_by(WikiDocumentPageTask.document_id)
        )
    )
    documents, states = lock_documents(session, kb_id, {task.document_id for task in tasks})
    page = session.scalar(
        select(WikiPage)
        .where(WikiPage.id == as_uuid(page_id), WikiPage.kb_id == as_uuid(kb_id))
        .with_for_update()
    )
    if not tasks and page.status != "cleanup_pending":
        return False
    if retry is None:
        retry = _attempt(page, error, settings, deferred=deferred)
    participants = (
        {(as_uuid(p["document_id"]), p["requested_version"], as_uuid(p["subtask_id"]))
         for p in snapshot["participants"]}
        if snapshot and "participants" in snapshot else None
    )
    eligible = []
    for task in tasks:
        state = states[task.document_id]
        if not state or task.requested_version != state.requested_version:
            task.status = "superseded"
            continue
        # A provisional plan did not supply evidence or take part in this execution.
        if not state.plan_ready or state.mapped_version != state.requested_version:
            continue
        if participants is not None and (
            task.document_id, task.requested_version, task.subtask_id
        ) not in participants:
            continue
        eligible.append(task)
    page.error_code = error.code
    if not retry:
        page.status = "updating" if len(eligible) < len(tasks) else "failed"
        page.row_version += 1
    for task in eligible:
        document, state = documents[task.document_id], states[task.document_id]
        if not state or task.requested_version != state.requested_version:
            task.status = "superseded"
            continue
        if document.run == 2 or state.status == "cancelled":
            task.status = "cancelled"
            continue
        next_status = "retry_wait" if retry else "failed"
        changed = task.error_code != error.code or task.status != next_status
        task.error_code, task.error_stage, task.status = error.code, "reducing", next_status
        field = "defer_count" if deferred else "failure_attempt"
        setattr(task, field, getattr(task, field) + 1)
        if changed:
            append_log(document, f"Wiki page {page.id}: {error.code}; retry={str(retry).lower()}.")
    session.flush()
    for doc_id in {task.document_id for task in eligible}:
        document = documents[doc_id]
        if states[doc_id]:
            settle_progress(session, document, states[doc_id])
    return retry
