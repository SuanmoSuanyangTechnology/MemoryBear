"""页面投递归属与新素材交接，所有写入使用既有知识库锁序。"""

from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.owned.wiki import WikiDocumentPageTask, WikiPage, WikiReduceDelivery, utcnow
from ...rag.wiki.config import TERMINAL_TASK_STATUSES, WikiError, WikiSuperseded
from . import failures, pages
from .common import as_uuid, lock_knowledge


def _message(row):
    return {
        key: str(getattr(row, key)) for key in ("kb_id", "page_id", "delivery_id", "attempt_id")
    }


def _new_turn(row):
    row.delivery_id, row.attempt_id = uuid4(), uuid4()
    row.status = "queued"
    row.cas_used = row.compact_used = row.attempts = 0
    row.deadline_at = None
    return _message(row)


async def clear_deliveries_for_knowledge(db: AsyncSession, kb_id: UUID) -> None:
    """调用方持有知识库行锁，在同一事务清理该库投递。"""
    await db.execute(delete(WikiReduceDelivery).where(WikiReduceDelivery.kb_id == kb_id))


def register(session, kb_id, page_ids):
    if not page_ids:
        return []
    knowledge, scope = lock_knowledge(session, kb_id, allow_deleted=True)
    if knowledge.status == 2:
        return []
    messages = []
    for page_id in sorted(set(page_ids), key=str):
        row = session.get(WikiReduceDelivery, as_uuid(page_id))
        if row is None:
            row = WikiReduceDelivery(**scope, page_id=as_uuid(page_id), input_revision=1)
            session.add(row)
            messages.append(_new_turn(row))
        else:
            row.input_revision += 1
    session.flush()
    return messages


def _owned(session, message, statuses):
    lock_knowledge(session, message["kb_id"])
    row = session.get(WikiReduceDelivery, as_uuid(message["page_id"]), with_for_update=True)
    if row is None or _message(row) != message or row.status not in statuses:
        raise WikiSuperseded("WIKI_DELIVERY_SUPERSEDED", retryable=False)
    return row


def claim(session, message, seconds):
    row = _owned(session, message, {"queued", "retry_wait"})
    row.status, row.attempts = "running", row.attempts + 1
    if row.deadline_at is None:
        row.deadline_at = utcnow() + timedelta(seconds=seconds)
    return row.input_revision, row.deadline_at


def read_attempt(session, message):
    row = _owned(session, message, {"running"})
    return row.input_revision, pages.load_reduce(session, message["kb_id"], message["page_id"])


def spend_compaction(session, message, limit):
    row = _owned(session, message, {"running"})
    if row.compact_used >= limit or utcnow() >= row.deadline_at:
        raise WikiError("WIKI_PAGE_BUDGET_EXHAUSTED", retryable=False)
    row.compact_used += 1


def retry_cas(session, message, limit):
    row = _owned(session, message, {"running"})
    if row.cas_used >= limit or utcnow() >= row.deadline_at:
        return False
    row.cas_used += 1
    return True


def _finish(session, row, seen):
    if row.input_revision > seen:
        return _new_turn(row)
    session.delete(row)
    return None


def commit(session, message, seen, snapshot, result):
    row = _owned(session, message, {"running"})
    if utcnow() >= row.deadline_at:
        raise WikiError("WIKI_PAGE_TIME_EXHAUSTED", retryable=False)
    published = pages.commit_reduce(session, snapshot, result) if snapshot is not None else None
    return published, _finish(session, row, seen)


def fail(session, message, seen, snapshot, error, settings):
    row = _owned(session, message, {"running"})
    retry = (
        error.retryable
        and row.attempts < settings.kb_wiki_failure_attempts
        and utcnow() < row.deadline_at
    )
    if retry:
        row.status, row.attempt_id = "retry_wait", uuid4()
        return _message(row), settings.kb_wiki_retry_delay_seconds
    page = session.get(WikiPage, row.page_id)
    cleanup = page is not None and page.status == "cleanup_pending"
    if row.input_revision > seen and snapshot is None:
        # 首次读取前已有新素材，交给后继统一结算。
        snapshot = {"participants": []}
    failures.page_failure(
        session, row.kb_id, row.page_id, error, settings, snapshot=snapshot, terminal=True
    )
    if cleanup:
        page.status = "cleanup_pending"
    return _finish(session, row, seen), 0


def manual_rerun(session, kb_id, page_id):
    """调用方持页面租约；只恢复这一个页面。"""
    _, scope = lock_knowledge(session, kb_id)
    page = session.scalar(
        select(WikiPage).where(WikiPage.kb_id == scope["kb_id"], WikiPage.id == as_uuid(page_id))
    )
    if page is None:
        return None
    pending = session.scalar(
        select(WikiDocumentPageTask.subtask_id)
        .where(
            WikiDocumentPageTask.kb_id == page.kb_id,
            WikiDocumentPageTask.page_id == page.id,
            WikiDocumentPageTask.status.not_in(TERMINAL_TASK_STATUSES),
        )
        .limit(1)
    )
    if not pending and page.status != "cleanup_pending":
        return None
    row = session.get(WikiReduceDelivery, page.id, with_for_update=True)
    if row is None:
        return register(session, kb_id, [page.id])[0]
    if row.status in {"queued", "retry_wait"}:
        return _message(row)
    return _new_turn(row)
