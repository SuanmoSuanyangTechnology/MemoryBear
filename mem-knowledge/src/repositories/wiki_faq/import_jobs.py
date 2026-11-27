"""FAQ 导入的短事务：阶段、批次和实际写入共同决定业务结果。"""

import logging
import uuid
from datetime import timedelta

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import DataError, IntegrityError

from ...application.config import DEFAULT_FAQ_IMPORT_PENDING_TIMEOUT_SECONDS
from ...errors import KnowledgeError
from ...models.owned import WikiFAQ
from ...models.owned.wiki_faq_import import WikiFAQImportJob as Job
from ...rag.wiki.config import WikiError
from ...utils.datetime_utils import utcnow_naive
from ..wiki.common import lock_knowledge
from ..wiki.read import begin_read
from .common import scoped

logger = logging.getLogger(__name__)

TERMINAL = frozenset({"succeeded", "partially_succeeded", "failed"})
PENDING_TIMEOUT_MESSAGE = "导入任务等待执行超时，请重新导入。"


def scope_of(job):
    return {key: getattr(job, key) for key in ("kb_id", "tenant_id", "workspace_id")}


def snapshot(job):
    return {col.name: getattr(job, col.name) for col in Job.__table__.columns}


def view(job):
    done = job.status in TERMINAL
    return {
        "task_id": str(job.id),
        "status": job.status,
        "stage": job.stage,
        "progress": {"total_count": job.total_count, "processed_count": job.processed_count},
        "summary": job.summary if done else None,
        "failure": job.failure if done else None,
        "failed_entries": job.failed_entries if done else None,
    }


def validate_scope(session, scope, *, repeatable_read=True):
    try:
        kb = begin_read(session, scope, repeatable_read=repeatable_read)
    except WikiError as exc:
        raise KnowledgeError.from_code("KB_RESOURCE_NOT_FOUND", status_code=404) from exc
    if kb.is_folder:
        raise KnowledgeError.from_code("KB_RESOURCE_NOT_FOUND", status_code=404)
    return {"embedding_id": kb.embedding_id}


def _pending_cutoff(timeout_seconds):
    return utcnow_naive() - timedelta(seconds=timeout_seconds)


def _pending_expired(job, cutoff):
    return job.status == "pending" and job.created_at <= cutoff


def _find_active_task_id(session, scope, cutoff=None) -> uuid.UUID | None:
    query = select(Job.id).where(
        *scoped(Job, scope),
        Job.status.in_(["pending", "running"]),
    )
    if cutoff is not None:
        query = query.where(or_(Job.status == "running", Job.created_at > cutoff))
    return session.scalar(query.limit(1))


def active_task_id(
    session, scope, timeout_seconds=DEFAULT_FAQ_IMPORT_PENDING_TIMEOUT_SECONDS
) -> uuid.UUID | None:
    validate_scope(session, scope)
    return _find_active_task_id(session, scope, _pending_cutoff(timeout_seconds))


def _expire_pending(session, scope, timeout_seconds):
    rows = session.scalars(
        select(Job)
        .where(
            *scoped(Job, scope),
            Job.status == "pending",
            Job.created_at <= _pending_cutoff(timeout_seconds),
        )
        .order_by(Job.id)
        .with_for_update()
    )
    return [_mark_failed(session, row, PENDING_TIMEOUT_MESSAGE) for row in rows]


def prepare_submission(session, scope, timeout_seconds):
    validate_scope(session, scope, repeatable_read=False)
    expired = _expire_pending(session, scope, timeout_seconds)
    return _find_active_task_id(session, scope), expired


def create(
    session,
    scope,
    actor_id,
    task_id,
    mode,
    payload_key,
    total,
    timeout_seconds=DEFAULT_FAQ_IMPORT_PENDING_TIMEOUT_SECONDS,
):
    # 沿用 Worker 的任务行 → 知识库行锁顺序。
    expired = _expire_pending(session, scope, timeout_seconds)
    try:
        lock_knowledge(session, scope["kb_id"], scope)
    except WikiError as exc:
        raise KnowledgeError.from_code("KB_RESOURCE_NOT_FOUND", status_code=404) from exc
    if _find_active_task_id(session, scope) is not None:
        raise KnowledgeError.from_code("KB_FAQ_IMPORT_BUSY", status_code=409)
    row = Job(
        id=task_id,
        **scope,
        actor_id=actor_id,
        mode=mode,
        payload_key=payload_key,
        status="pending",
        stage="queued",
        total_count=total,
        processed_count=0,
        candidates=[],
        failed_entries=[],
        cleanup_ids=[],
    )
    session.add(row)
    session.flush()
    return expired


def progress(session, scope, task_id, timeout_seconds=DEFAULT_FAQ_IMPORT_PENDING_TIMEOUT_SECONDS):
    validate_scope(session, scope, repeatable_read=False)
    query = select(Job).where(Job.id == task_id, *scoped(Job, scope))
    row = session.scalar(query)
    cutoff = _pending_cutoff(timeout_seconds)
    if row is not None and _pending_expired(row, cutoff):
        # 与正在领取的 Worker 同步，避免把已经领取的任务显示为失败。
        row = session.scalar(
            query.with_for_update(read=True).execution_options(populate_existing=True)
        )
    if row is None:
        raise KnowledgeError.from_code("KB_RESOURCE_NOT_FOUND", status_code=404)
    result = view(row)
    if _pending_expired(row, cutoff):
        failure, summary = _failure_details(session, row, PENDING_TIMEOUT_MESSAGE)
        result.update(
            status="failed", stage="finished", failure=failure, summary=summary, failed_entries=[]
        )
    return result


def load(session, task_id):
    row = session.get(Job, uuid.UUID(str(task_id)))
    return snapshot(row) if row else None


def claim(
    session,
    task_id,
    token,
    lease_seconds,
    timeout_seconds=DEFAULT_FAQ_IMPORT_PENDING_TIMEOUT_SECONDS,
):
    row = session.scalar(select(Job).where(Job.id == task_id).with_for_update())
    if row is None or row.status in TERMINAL:
        return None
    if _pending_expired(row, _pending_cutoff(timeout_seconds)):
        _mark_failed(session, row, PENDING_TIMEOUT_MESSAGE)
        return None
    if row.lease_expires_at and row.lease_expires_at > utcnow_naive():
        return {"busy": True}
    row.execution_token = token
    row.lease_expires_at = utcnow_naive() + timedelta(seconds=lease_seconds)
    row.status = "running"
    if row.stage == "queued":
        row.stage = "validating"
    session.flush()
    return snapshot(row)


def owned(session, task_id, token):
    row = session.scalar(select(Job).where(Job.id == task_id).with_for_update())
    if row is None or row.execution_token != token or row.status in TERMINAL:
        raise RuntimeError("FAQ_IMPORT_EXECUTION_SUPERSEDED")
    return row


def checkpoint(session, task_id, token, candidates, failures, processed, model_key, stage):
    row = owned(session, task_id, token)
    row.candidates, row.failed_entries = candidates, failures
    row.processed_count, row.model_key, row.stage = processed, model_key, stage


def entry_failure(index, item, stage, reason):
    return {
        "item_index": index + 1,
        "source_position": item["source_position"],
        # PostgreSQL JSONB 无法存储 NUL；仅替换失败明细里的原始问题。
        "standard_question": item["standard_question"]
        .strip()
        .replace("\x00", "\ufffd")
        .encode("utf-8", errors="replace")
        .decode("utf-8"),
        "stage": stage,
        "reason": reason,
    }


class ImportModelChanged(Exception):
    """提交阶段锁内再次确认的模型已变化。"""


def commit_faqs(session, task_id, token, items, model_id):
    job = owned(session, task_id, token)
    scope = scope_of(job)
    kb, _ = lock_knowledge(session, job.kb_id, scope)
    if kb.embedding_id != model_id:
        raise ImportModelChanged("FAQ_IMPORT_MODEL_CHANGED")
    old_ids = (
        list(
            session.scalars(
                select(WikiFAQ.id).where(
                    *scoped(WikiFAQ, scope),
                    WikiFAQ.deleted_at.is_(None),
                )
            )
        )
        if job.mode == "replace"
        else []
    )
    failures, cleanup = list(job.failed_entries), []
    success = 0
    for candidate in job.candidates:
        index, faq_id = candidate["index"], uuid.UUID(candidate["id"])
        item = items[index]
        try:
            with session.begin_nested():
                session.add(
                    WikiFAQ(
                        id=faq_id,
                        **scope,
                        origin="manual",
                        standard_question=item["standard_question"].strip(),
                        answers=[item["standard_answer"].strip()],
                        similar_questions=[],
                        negative_questions=[],
                        answer_strategy="all",
                        review_state="approved",
                        is_enabled=False,
                        content_revision=1,
                        row_version=1,
                        created_by=job.actor_id,
                        updated_by=job.actor_id,
                    )
                )
                session.flush()
            success += 1
        except (DataError, IntegrityError) as exc:
            logger.exception(
                "FAQ 条目写入失败 task_id=%s item_index=%s stage=writing error_type=%s",
                task_id,
                index + 1,
                type(exc).__name__,
            )
            failures.append(entry_failure(index, item, "writing", "问答数据写入失败，本条未导入。"))
            cleanup.append(str(faq_id))
    if success and old_ids:
        session.execute(
            update(WikiFAQ)
            .where(WikiFAQ.id.in_(old_ids))
            .values(
                deleted_at=utcnow_naive(),
                is_enabled=False,
                updated_at=utcnow_naive(),
                updated_by=job.actor_id,
                row_version=WikiFAQ.row_version + 1,
            )
        )
        cleanup.extend(map(str, old_ids))
    total_all = session.scalar(
        select(func.count())
        .select_from(WikiFAQ)
        .where(
            *scoped(WikiFAQ, scope),
            WikiFAQ.deleted_at.is_(None),
        )
    )
    job.status = (
        "succeeded"
        if success == job.total_count
        else "partially_succeeded"
        if success
        else "failed"
    )
    job.stage = "finished"
    job.summary = {
        "total_count": job.total_count,
        "success_count": success,
        "failure_count": job.total_count - success,
        "removed_count": len(old_ids) if success else 0,
        "total_all": total_all,
    }
    job.failed_entries = sorted(failures, key=lambda item: item["item_index"])
    job.cleanup_ids = cleanup
    job.execution_token = None
    job.lease_expires_at = None
    session.flush()
    return snapshot(job)


def cancel_pending_dispatch(session, task_id):
    row = session.scalar(select(Job).where(Job.id == task_id).with_for_update())
    if row is None:
        raise KnowledgeError.from_code("KB_RESOURCE_NOT_FOUND", status_code=404)
    if row.status != "pending":
        return None
    return _mark_failed(session, row, "导入任务派发失败，本批未导入。")


def fail(session, task_id, token, message):
    row = session.scalar(select(Job).where(Job.id == task_id).with_for_update())
    if row is None or row.status in TERMINAL:
        return snapshot(row) if row else None
    # 提交方只可撤销尚未被 Worker 领取的任务，迟到的派发回执不能覆盖执行结果。
    if (token is None and row.status != "pending") or row.execution_token != token:
        return snapshot(row)
    return _mark_failed(session, row, message)


def _failure_details(session, row, message):
    total = session.scalar(
        select(func.count())
        .select_from(WikiFAQ)
        .where(
            *scoped(WikiFAQ, scope_of(row)),
            WikiFAQ.deleted_at.is_(None),
        )
    )
    return {"code": "KB_FAQ_IMPORT_FAILED", "message": message}, {
        "total_count": row.total_count,
        "success_count": 0,
        "failure_count": row.total_count,
        "removed_count": 0,
        "total_all": total,
    }


def _mark_failed(session, row, message):
    row.status, row.stage = "failed", "finished"
    row.failure, row.summary = _failure_details(session, row, message)
    row.cleanup_ids = [item["id"] for item in row.candidates]
    row.execution_token = None
    row.lease_expires_at = None
    session.flush()
    return snapshot(row)


def cleaned(session, task_id):
    row = session.get(Job, task_id)
    if row and row.status in TERMINAL:
        row.candidates, row.cleanup_ids = [], []


def release_after_db_error(session, task_id, token):
    # 上次 commit 的结果可能已生效。只释放本次执行令牌，不修改进度或终态。
    session.execute(
        update(Job)
        .where(Job.id == task_id, Job.execution_token == token, Job.status == "running")
        .values(execution_token=None, lease_expires_at=None)
    )


def release_for_reindex(session, task_id, token):
    row = owned(session, task_id, token)
    row.processed_count = 0
    row.candidates, row.failed_entries = [], []
    row.model_key = None
    row.stage = "validating"
    row.execution_token, row.lease_expires_at = None, None
