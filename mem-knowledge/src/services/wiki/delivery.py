"""独立页面 Worker 的有限执行与 CAS 发布。"""

import logging
import math

from billiard.exceptions import SoftTimeLimitExceeded

from ...models.owned.wiki import utcnow
from ...rag.wiki.config import PAGE_QUEUE, PAGE_TASK, WikiDeferred, WikiError, WikiSuperseded
from ...rag.wiki.diagnostics import diagnostic_json, exception_details
from ...rag.wiki.pipeline import generate_page
from ...repositories.wiki import deliveries
from .execution import dispatcher
from .lease import page_lease
from .runtime import make_model, transaction

logger = logging.getLogger(__name__)


def send_page(runtime, message, delay=0, remaining=None):
    seconds = runtime.settings.kb_wiki_page_seconds if remaining is None else remaining
    dispatcher(runtime).send_sync(
        PAGE_TASK,
        kwargs=message,
        queue=PAGE_QUEUE,
        task_id=message["attempt_id"],
        countdown=delay,
        soft_time_limit=max(1, min(runtime.settings.kb_task_soft_time_limit_seconds, seconds)),
    )


def run_delivery(runtime, message):
    from ..wiki_faq import publish_pending

    settings = runtime.settings
    seen, deadline = transaction(runtime, deliveries.claim, message, settings.kb_wiki_page_seconds)
    snapshot = None

    def check_deadline():
        if utcnow() >= deadline:
            raise WikiError("WIKI_PAGE_TIME_EXHAUSTED", retryable=False)

    def before_request(stage):
        check_deadline()
        if stage == "wiki_evidence_compact_v1":
            transaction(
                runtime, deliveries.spend_compaction, message, settings.kb_wiki_compact_calls
            )

    delay = 0
    try:
        with page_lease(runtime.redis.sync_client(), message["kb_id"], message["page_id"]) as owned:
            while True:
                seen, snapshot = transaction(runtime, deliveries.read_attempt, message)
                check_deadline()
                model = None
                if snapshot and snapshot["sources"] and snapshot["page_type"] != "summary":
                    model = make_model(runtime, snapshot)
                    model.before_request, model.check_deadline = before_request, check_deadline
                result = generate_page(model, snapshot) if snapshot else None
                owned()
                try:
                    result, successor = transaction(
                        runtime, deliveries.commit, message, seen, snapshot, result
                    )
                    break
                except WikiDeferred as exc:
                    if exc.code not in {
                        "WIKI_CAS_CONFLICT",
                        "WIKI_INPUT_CHANGED",
                        "WIKI_PARTICIPANT_CHANGED",
                    }:
                        raise
                    if not transaction(
                        runtime, deliveries.retry_cas, message, settings.kb_wiki_cas_retries
                    ):
                        raise WikiError("WIKI_CAS_BUDGET_EXHAUSTED", retryable=False) from exc
                    logger.info("Wiki 页面发生版本冲突，将重新读取素材 kb_id=%s page_id=%s "
                                "delivery_id=%s attempt_id=%s reason=%s",
                                message["kb_id"], message["page_id"], message["delivery_id"],
                                message["attempt_id"], exc.code)
    except WikiSuperseded:
        raise
    except Exception as exc:
        error = exc if isinstance(exc, WikiError) else WikiError("WIKI_EXECUTION_FAILED")
        if isinstance(exc, SoftTimeLimitExceeded) or utcnow() >= deadline:
            error = WikiError("WIKI_PAGE_TIME_EXHAUSTED", retryable=False)
        snapshot = getattr(exc, "wiki_snapshot", snapshot)
        error.diagnostics = {**exception_details(exc), **message, "error_code": error.code}
        logger.error("Wiki 页面生成失败 %s", diagnostic_json(error.diagnostics))
        successor, delay = transaction(
            runtime, deliveries.fail, message, seen, snapshot, error, settings
        )
        result = {"status": "retry_wait" if delay else "failed", "error_code": error.code}
    publish_pending(runtime, message["kb_id"], message["page_id"])
    if successor:
        if delay:
            remaining = math.ceil((deadline - utcnow()).total_seconds()) - delay
            send_page(runtime, successor, delay, remaining)
        else:
            send_page(runtime, successor, 0)
    return result


def rerun_page(runtime, kb_id, page_id):
    with page_lease(runtime.redis.sync_client(), kb_id, page_id):
        message = transaction(runtime, deliveries.manual_rerun, kb_id, page_id)
    if message:
        send_page(runtime, message)
    return bool(message)
