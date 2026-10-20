"""首次生成任务与 Wiki 发布同事务登记；消息重投复用身份和 v1 快照。"""

import uuid
from datetime import timedelta

from sqlalchemy import select

from ...models.owned.wiki import WikiPage
from ...models.owned.wiki_faq import WikiFAQ, WikiFAQGenerationJob, WikiPageVersion
from ...rag.faq.contracts import FAQ_PROMPT_VERSION
from ...rag.wiki.config import WikiDeferred, WikiError, fingerprint
from ...utils.datetime_utils import utcnow_naive
from ..wiki.common import as_uuid, lock_knowledge
from .common import conflict, missing, scoped
from .read import job_data


def record_publication(session, scope, page, previous_version, generation_config):
    """调用方已锁定知识库/页面，并已设置本次 published_sources。"""
    if page.version == previous_version:
        return
    version = WikiPageVersion(
        id=uuid.uuid4(),
        **scope,
        wiki_page_id=page.id,
        page_version=page.version,
        title=page.title,
        content=page.content,
        summary=page.summary,
        page_type=page.page_type,
        aliases=list(page.aliases),
        published_sources=list(page.published_sources),
        content_hash=page.published_content_hash,
        published_at=utcnow_naive(),
    )
    session.add(version)
    session.flush()
    if previous_version == 0 and page.version == 1:
        config = {
            "llm_id": generation_config["llm_id"],
            "language": generation_config["language"],
            "prompt_version": FAQ_PROMPT_VERSION,
        }
        session.add(
            WikiFAQGenerationJob(
                id=uuid.uuid4(),
                **scope,
                wiki_page_id=page.id,
                trigger_kind="first_publish",
                source_snapshot_id=version.id,
                config_snapshot=config,
                config_hash=fingerprint(config),
                status="pending",
                task_id=uuid.uuid4(),
                publish_confirmed=False,
                row_version=1,
                failure_attempt=0,
                dispatch_attempt=0,
            )
        )


def _job(session, kb_id, job_id, scope=None):
    _, actual_scope = lock_knowledge(session, kb_id, scope)
    job = session.scalar(
        select(WikiFAQGenerationJob)
        .where(
            *scoped(WikiFAQGenerationJob, actual_scope),
            WikiFAQGenerationJob.id == as_uuid(job_id),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if job is None:
        missing()
    return job, actual_scope


def _delivery(job):
    return {
        "kb_id": str(job.kb_id),
        "job_id": str(job.id),
        "task_id": str(job.task_id),
        "expected_row_version": job.row_version,
    }


def pending_deliveries(session, kb_id, page_id):
    _, scope = lock_knowledge(session, kb_id)
    rows = session.scalars(
        select(WikiFAQGenerationJob).where(
            *scoped(WikiFAQGenerationJob, scope),
            WikiFAQGenerationJob.wiki_page_id == as_uuid(page_id),
            WikiFAQGenerationJob.status.in_(("pending", "retry_wait")),
            WikiFAQGenerationJob.publish_confirmed.is_(False),
        )
    )
    return [_delivery(row) for row in rows]


def record_dispatch(session, delivery, *, succeeded):
    job, _ = _job(session, delivery["kb_id"], delivery["job_id"])
    if (
        str(job.task_id) != delivery["task_id"]
        or job.row_version != delivery["expected_row_version"]
    ):
        # 自动重试复用 task_id；上一轮迟到回执不能确认新一轮的消息交接。
        return
    job.dispatch_attempt += 1
    if succeeded:
        job.publish_confirmed = True
    elif not job.publish_confirmed and job.status == "pending":
        job.error_code, job.error_stage = "KB_FAQ_DISPATCH_FAILED", "dispatch"
    job.row_version += 1


def retry(session, scope, job_id, expected_row_version):
    job, _ = _job(session, scope["kb_id"], job_id, scope)
    if job.row_version != expected_row_version:
        conflict()
    if job.status == "running" and job.lease_expires_at and job.lease_expires_at > utcnow_naive():
        conflict("KB_FAQ_JOB_BUSY")
    if job.status in {"succeeded", "cancelled"}:
        conflict("KB_FAQ_JOB_TERMINAL")
    # 新投递令牌令旧任务失去回写资格；仍复用同一 job/v1，而不是另起生成轮次。
    job.status, job.task_id, job.execution_token = "pending", uuid.uuid4(), None
    job.publish_confirmed, job.lease_expires_at = False, None
    job.failure_attempt = 0
    job.error_code, job.error_stage = None, None
    job.row_version += 1
    return _delivery(job)


def _claim_failure(job, code, message):
    """返回本次新判定的业务失败，与重复投递无需执行的 None 区分。"""
    job.status, job.error_code, job.error_stage = "failed", code, "claim"
    job.execution_token, job.lease_expires_at = None, None
    job.row_version += 1
    return _failure_result(job, message)


def _failure_result(job, message):
    return {
        "job_id": str(job.id),
        "wiki_page_id": str(job.wiki_page_id),
        "status": "failed",
        "error_code": job.error_code,
        "error_stage": job.error_stage,
        "error_message": message,
    }


def recover(session, kb_id, job_id, task_id, recovery, *, failure_limit):
    """仅恢复已知执行的状态写入，不把数据库故障当作未知 Worker 失联。"""
    job, _ = _job(session, kb_id, job_id)
    if (
        str(job.task_id) != str(task_id)
        or job.status != "running"
        or str(job.execution_token) != recovery["execution_token"]
    ):
        # 提交可能已成功，或任务已被重投；迟到恢复不得释放其他执行的租约。
        return None
    failure = recovery.get("failure")
    if failure is not None:
        again = _fail_job(
            job,
            failure["code"],
            failure["stage"],
            retryable=failure["retryable"],
            failure_limit=failure_limit,
        )
        if not again:
            return _failure_result(job, "数据库恢复后已持久化原执行的业务失败")
    else:
        # 已知执行已退出；原令牌只允许释放一次，不增加业务失败计数。
        job.status, job.execution_token, job.lease_expires_at = "retry_wait", None, None
        job.publish_confirmed = False
        job.error_code, job.error_stage = "WIKI_STATE_UNAVAILABLE", "faq_state_recovery"
        job.row_version += 1
    return None


def claim(session, kb_id, job_id, task_id, lease_seconds, failure_limit, *, execution_token=None):
    job, scope = _job(session, kb_id, job_id)
    if str(job.task_id) != str(task_id) or job.status in {"succeeded", "failed", "cancelled"}:
        return None
    now = utcnow_naive()
    if job.status == "running" and job.lease_expires_at and job.lease_expires_at > now:
        raise WikiDeferred("WIKI_FAQ_JOB_BUSY")
    if job.status == "running":
        job.failure_attempt += 1
        if job.failure_attempt >= failure_limit:
            return _claim_failure(
                job,
                "KB_FAQ_WORKER_LOST",
                "Worker 执行租约已过期，领取恢复次数达到失败预算",
            )
    page = session.scalar(
        select(WikiPage).where(
            WikiPage.id == job.wiki_page_id,
            *scoped(WikiPage, scope),
        )
    )
    if page is None or page.status == "unavailable":
        return _claim_failure(
            job,
            "KB_FAQ_SOURCE_UNAVAILABLE",
            "FAQ 来源页面不存在或已不可用",
        )
    snapshot = session.scalar(
        select(WikiPageVersion).where(
            *scoped(WikiPageVersion, scope),
            WikiPageVersion.id == job.source_snapshot_id,
            WikiPageVersion.wiki_page_id == job.wiki_page_id,
            WikiPageVersion.page_version == 1,
        )
    )
    if snapshot is None:
        conflict("KB_FAQ_SOURCE_UNAVAILABLE")
    job.status = "running"
    job.execution_token = as_uuid(execution_token) if execution_token is not None else uuid.uuid4()
    job.lease_expires_at = now + timedelta(seconds=lease_seconds)
    job.publish_confirmed = True
    job.error_code, job.error_stage = None, None
    job.row_version += 1
    return {
        **{k: str(v) for k, v in scope.items()},
        **job.config_snapshot,
        "job_id": str(job.id),
        "page_id": str(job.wiki_page_id),
        "execution_token": str(job.execution_token),
        "source_snapshot_id": str(snapshot.id),
        "title": snapshot.title,
        "content": snapshot.content,
        "summary": snapshot.summary,
    }


def complete(session, snapshot, items):
    job, scope = _job(session, snapshot["kb_id"], snapshot["job_id"])
    if job.status != "running" or str(job.execution_token) != snapshot["execution_token"]:
        return None
    if not session.scalar(
        select(WikiPage.id).where(
            WikiPage.id == job.wiki_page_id,
            *scoped(WikiPage, scope),
            WikiPage.status != "unavailable",
        )
    ):
        raise WikiError(
            "KB_FAQ_SOURCE_UNAVAILABLE",
            retryable=False,
            diagnostics={"stage": "faq_commit", "page_id": str(job.wiki_page_id)},
        )
    for item in items:
        session.add(
            WikiFAQ(
                id=uuid.uuid4(),
                **scope,
                wiki_page_id=job.wiki_page_id,
                origin="auto",
                generation_job_id=job.id,
                generated_wiki_snapshot_id=job.source_snapshot_id,
                standard_question=item["standard_question"],
                answers=[item["standard_answer"]],
                similar_questions=[],
                negative_questions=[],
                answer_strategy="all",
                review_state="pending",
                is_enabled=False,
                content_revision=1,
                row_version=1,
            )
        )
    job.status, job.result_count = "succeeded", len(items)
    job.execution_token, job.lease_expires_at = None, None
    job.error_code, job.error_stage = None, None
    job.row_version += 1
    session.flush()
    return job_data(job)


def fail(session, snapshot, code, stage, *, retryable, failure_limit):
    job, _ = _job(session, snapshot["kb_id"], snapshot["job_id"])
    if job.status != "running" or str(job.execution_token) != snapshot["execution_token"]:
        return False
    return _fail_job(job, code, stage, retryable=retryable, failure_limit=failure_limit)


def _fail_job(job, code, stage, *, retryable, failure_limit):
    job.failure_attempt += 1
    again = retryable and job.failure_attempt < failure_limit
    job.status = "retry_wait" if again else "failed"
    if again:
        # self.retry 投递失败也能由页面重投/显式重试恢复；消费领取后再确认交接。
        job.publish_confirmed = False
    job.error_code, job.error_stage = code[:80], stage[:80]
    job.execution_token, job.lease_expires_at = None, None
    job.row_version += 1
    return again
