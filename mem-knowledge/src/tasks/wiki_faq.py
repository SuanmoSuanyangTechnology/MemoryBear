"""Wiki FAQ 首次生成任务，持久执行令牌防止重投和迟到结果重复落库。"""

from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as DatabaseTimeoutError

from ..rag.faq.generation import generate
from ..rag.wiki.config import WikiDeferred, WikiError, WikiSuperseded
from ..repositories.wiki_faq import jobs
from ..runtime import get_worker_runtime
from ..usage_context import bind_usage
from .celery_app import celery_app
from .observability import (
    BusinessOutcome,
    StructuredLogSink,
    observe_task,
    task_context_from_current_task,
)
from .wiki import _report_error


@celery_app.task(bind=True, name="mem_knowledge.tasks.generate_wiki_faq", max_retries=None)
@bind_usage("kb", "kb_id")
def generate_wiki_faq(self, kb_id, job_id):
    from ..services.wiki.runtime import make_model, transaction

    runtime = get_worker_runtime()
    settings = runtime.settings
    context = task_context_from_current_task(role="wiki_worker", knowledge_id=kb_id)
    with observe_task(
        context, sinks=[StructuredLogSink()], heartbeat_seconds=settings.kb_task_heartbeat_seconds
    ) as run:
        snapshot = None
        try:
            claimed = transaction(
                runtime,
                jobs.claim,
                kb_id,
                job_id,
                self.request.id,
                settings.kb_task_time_limit_seconds,
                settings.kb_wiki_failure_attempts,
            )
            if claimed is None:
                run.finish(BusinessOutcome.SKIPPED)
                return {"job_id": job_id, "result": "no_work"}
            if claimed.get("status") == "failed":
                error = WikiError(
                    claimed["error_code"],
                    retryable=False,
                    diagnostics={
                        "stage": claimed["error_stage"],
                        "job_id": job_id,
                        "wiki_page_id": claimed["wiki_page_id"],
                        "error_message": claimed["error_message"],
                    },
                )
                _report_error(run, error)
                run.finish(BusinessOutcome.FAILURE, error_code=error.code)
                return claimed
            snapshot = claimed
            model = make_model(runtime, snapshot)
            result = generate(model, snapshot)
            committed = transaction(runtime, jobs.complete, snapshot, result)
            run.finish(BusinessOutcome.SUCCESS)
            return committed
        except WikiSuperseded as exc:
            run.finish(BusinessOutcome.ABORTED, error_code=exc.code)
            return {"job_id": job_id, "status": "cancelled", "error_code": exc.code}
        except (OperationalError, InterfaceError, DatabaseTimeoutError) as exc:
            # 状态库尚不可用时不能终结消息；业务失败预算只由持久 job 负责。
            _report_error(run, exc, code="WIKI_STATE_UNAVAILABLE")
            run.finish(BusinessOutcome.RETRY, error_code="WIKI_STATE_UNAVAILABLE")
            raise self.retry(
                exc=RuntimeError("WIKI_STATE_UNAVAILABLE"),
                countdown=settings.kb_wiki_retry_delay_seconds,
                max_retries=None,
            ) from exc
        except WikiDeferred as exc:
            _report_error(run, exc)
            run.finish(BusinessOutcome.RETRY, error_code=exc.code)
            raise self.retry(
                exc=RuntimeError(exc.code), countdown=settings.kb_wiki_retry_delay_seconds
            ) from exc
        except Exception as exc:
            error = _report_error(run, exc)
            if snapshot is None:
                # 数据库暂时不可用已单独处理；其他领取异常保留有限的消息重试。
                run.finish(BusinessOutcome.RETRY, error_code=error.code)
                raise self.retry(
                    exc=RuntimeError(error.code),
                    countdown=settings.kb_wiki_retry_delay_seconds,
                    max_retries=settings.kb_wiki_failure_attempts,
                ) from exc
            retryable = error.retryable if isinstance(exc, WikiError) else True
            try:
                again = transaction(
                    runtime,
                    jobs.fail,
                    snapshot,
                    error.code,
                    (error.diagnostics or {}).get("stage", "faq_generate"),
                    retryable=retryable,
                    failure_limit=settings.kb_wiki_failure_attempts,
                )
            except Exception as state_error:
                # 失败状态尚未持久化时继续交接消息，不能只 ACK 后留下 running job。
                _report_error(run, state_error, code="WIKI_STATE_UNAVAILABLE")
                run.finish(BusinessOutcome.RETRY, error_code="WIKI_STATE_UNAVAILABLE")
                raise self.retry(
                    exc=RuntimeError("WIKI_STATE_UNAVAILABLE"),
                    countdown=settings.kb_wiki_retry_delay_seconds,
                ) from state_error
            run.finish(
                BusinessOutcome.RETRY if again else BusinessOutcome.FAILURE, error_code=error.code
            )
            if again:
                raise self.retry(
                    exc=RuntimeError(error.code), countdown=settings.kb_wiki_retry_delay_seconds
                ) from exc
            return {"job_id": job_id, "status": "failed", "error_code": error.code}
