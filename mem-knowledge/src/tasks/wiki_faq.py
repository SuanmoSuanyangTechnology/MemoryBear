"""Wiki FAQ 首次生成任务，持久执行令牌防止重投和迟到结果重复落库。"""

import uuid

from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as DatabaseTimeoutError

from ..rag.faq.contracts import FAQ_QUEUE
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
def generate_wiki_faq(self, kb_id, job_id, recovery=None):
    from ..services.wiki.runtime import make_model, transaction

    runtime = get_worker_runtime()
    settings = runtime.settings
    delivery = {"kb_id": kb_id, "job_id": job_id}
    context = task_context_from_current_task(role="faq_worker", knowledge_id=kb_id)
    with observe_task(
        context, sinks=[StructuredLogSink()], heartbeat_seconds=settings.kb_task_heartbeat_seconds
    ) as run:
        snapshot = None
        claim_token = None
        try:
            claimed = None
            if recovery is not None:
                claimed = transaction(
                    runtime,
                    jobs.recover,
                    kb_id,
                    job_id,
                    self.request.id,
                    recovery,
                    failure_limit=settings.kb_wiki_failure_attempts,
                )
                recovery = None
            if claimed is None:
                # 领取提交的回执也可能丢失，事务前生成令牌才能恢复这一不确定窗口。
                claim_token = str(uuid.uuid4())
                claimed = transaction(
                    runtime,
                    jobs.claim,
                    kb_id,
                    job_id,
                    self.request.id,
                    settings.kb_task_time_limit_seconds,
                    settings.kb_wiki_failure_attempts,
                    execution_token=claim_token,
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
            if committed is None:
                run.finish(BusinessOutcome.SKIPPED)
                return {"job_id": job_id, "result": "superseded"}
            run.finish(BusinessOutcome.SUCCESS)
            return committed
        except WikiSuperseded as exc:
            run.finish(BusinessOutcome.ABORTED, error_code=exc.code)
            return {"job_id": job_id, "status": "cancelled", "error_code": exc.code}
        except (OperationalError, InterfaceError, DatabaseTimeoutError) as exc:
            # 保留已知执行令牌，让恢复消息释放原租约，而不是等待过期并消耗失联预算。
            if snapshot is not None:
                recovery = {"execution_token": snapshot["execution_token"]}
            elif claim_token is not None:
                recovery = {"execution_token": claim_token}
            _report_error(run, exc, code="WIKI_STATE_UNAVAILABLE")
            run.finish(BusinessOutcome.RETRY, error_code="WIKI_STATE_UNAVAILABLE")
            raise self.retry(
                queue=FAQ_QUEUE,
                exc=RuntimeError("WIKI_STATE_UNAVAILABLE"),
                args=(),
                kwargs={**delivery, "recovery": recovery},
                countdown=settings.kb_wiki_retry_delay_seconds,
                max_retries=None,
            ) from exc
        except WikiDeferred as exc:
            _report_error(run, exc)
            run.finish(BusinessOutcome.RETRY, error_code=exc.code)
            raise self.retry(
                queue=FAQ_QUEUE,
                exc=RuntimeError(exc.code),
                args=(),
                kwargs=delivery,
                countdown=settings.kb_wiki_retry_delay_seconds,
            ) from exc
        except Exception as exc:
            error = _report_error(run, exc)
            if snapshot is None:
                # 数据库暂时不可用已单独处理；其他领取异常保留有限的消息重试。
                run.finish(BusinessOutcome.RETRY, error_code=error.code)
                raise self.retry(
                    queue=FAQ_QUEUE,
                    exc=RuntimeError(error.code),
                    args=(),
                    kwargs={**delivery, "recovery": recovery},
                    countdown=settings.kb_wiki_retry_delay_seconds,
                    max_retries=settings.kb_wiki_failure_attempts,
                ) from exc
            retryable = error.retryable if isinstance(exc, WikiError) else True
            error_stage = (error.diagnostics or {}).get("stage", "faq_generate")
            try:
                again = transaction(
                    runtime,
                    jobs.fail,
                    snapshot,
                    error.code,
                    error_stage,
                    retryable=retryable,
                    failure_limit=settings.kb_wiki_failure_attempts,
                )
            except Exception as state_error:
                # 失败状态尚未持久化时继续交接消息，不能只 ACK 后留下 running job。
                _report_error(run, state_error, code="WIKI_STATE_UNAVAILABLE")
                run.finish(BusinessOutcome.RETRY, error_code="WIKI_STATE_UNAVAILABLE")
                raise self.retry(
                    queue=FAQ_QUEUE,
                    exc=RuntimeError("WIKI_STATE_UNAVAILABLE"),
                    args=(),
                    kwargs={
                        **delivery,
                        "recovery": {
                            "execution_token": snapshot["execution_token"],
                            "failure": {
                                "code": error.code,
                                "stage": error_stage,
                                "retryable": retryable,
                            },
                        },
                    },
                    countdown=settings.kb_wiki_retry_delay_seconds,
                    max_retries=None,
                ) from state_error
            run.finish(
                BusinessOutcome.RETRY if again else BusinessOutcome.FAILURE, error_code=error.code
            )
            if again:
                raise self.retry(
                    queue=FAQ_QUEUE,
                    exc=RuntimeError(error.code),
                    args=(),
                    kwargs=delivery,
                    countdown=settings.kb_wiki_retry_delay_seconds,
                ) from exc
            return {"job_id": job_id, "status": "failed", "error_code": error.code}
