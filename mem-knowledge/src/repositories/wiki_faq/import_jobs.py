"""FAQ 导入的短事务：阶段、批次和实际写入共同决定业务结果。"""

import logging
import uuid
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.exc import DataError, IntegrityError

from ...errors import KnowledgeError
from ...models.owned import Knowledge, WikiFAQ
from ...models.owned.wiki_faq_import import WikiFAQImportJob as Job
from ...rag.wiki.config import WikiError
from ...utils.datetime_utils import utcnow_naive
from ..wiki.common import lock_knowledge
from ..wiki.read import begin_read
from .common import scoped

logger = logging.getLogger(__name__)

TERMINAL = frozenset({"succeeded", "partially_succeeded", "failed"})


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


def validate_scope(session, scope):
    try:
        begin_read(session, scope)
    except WikiError as exc:
        raise KnowledgeError.from_code("KB_RESOURCE_NOT_FOUND", status_code=404) from exc
    kb = session.get(Knowledge, scope["kb_id"])
    if kb.is_folder:
        raise KnowledgeError.from_code("KB_RESOURCE_NOT_FOUND", status_code=404)
    return {"embedding_id": kb.embedding_id}


def create(session, scope, actor_id, task_id, mode, payload_key, total):
    lock_knowledge(session, scope["kb_id"], scope)
    active = session.scalar(
        select(Job.id)
        .where(
            *scoped(Job, scope),
            Job.status.in_(["pending", "running"]),
        )
        .limit(1)
    )
    if active:
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
    return view(row)


def progress(session, scope, task_id):
    validate_scope(session, scope)
    row = session.scalar(select(Job).where(Job.id == task_id, *scoped(Job, scope)))
    if row is None:
        raise KnowledgeError.from_code("KB_RESOURCE_NOT_FOUND", status_code=404)
    return view(row)


def load(session, task_id):
    row = session.get(Job, uuid.UUID(str(task_id)))
    return snapshot(row) if row else None


def claim(session, task_id, token, lease_seconds):
    row = session.scalar(select(Job).where(Job.id == task_id).with_for_update())
    if row is None or row.status in TERMINAL:
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
        "standard_question": item["standard_question"]
        .strip()
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
                        is_enabled=True,
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


def _mark_failed(session, row, message):
    total = session.scalar(
        select(func.count())
        .select_from(WikiFAQ)
        .where(
            *scoped(WikiFAQ, scope_of(row)),
            WikiFAQ.deleted_at.is_(None),
        )
    )
    row.status, row.stage = "failed", "finished"
    row.failure = {"code": "KB_FAQ_IMPORT_FAILED", "message": message}
    row.summary = {
        "total_count": row.total_count,
        "success_count": 0,
        "failure_count": row.total_count,
        "removed_count": 0,
        "total_all": total,
    }
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
