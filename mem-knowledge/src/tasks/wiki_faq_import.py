"""FAQ 导入 Worker，业务结果从 PG 任务记录读取。"""

from sqlalchemy.exc import InterfaceError, OperationalError

from ..runtime import get_worker_runtime
from .celery_app import celery_app
from .observability import StructuredLogSink, observe_task, task_context_from_current_task


@celery_app.task(bind=True, name="mem_knowledge.tasks.import_faqs", max_retries=None)
def import_faqs(self, task_id):
    from ..services.wiki_faq_import import execute

    runtime = get_worker_runtime()
    context = task_context_from_current_task(role="qa_import_worker")
    with observe_task(
        context,
        sinks=[StructuredLogSink()],
        heartbeat_seconds=runtime.settings.kb_task_heartbeat_seconds,
    ):
        try:
            busy = runtime.run_async(lambda: execute(runtime, task_id))
        except (InterfaceError, OperationalError) as exc:
            raise self.retry(exc=exc, countdown=30) from exc
    if busy:
        raise self.retry(countdown=30)
    return {"task_id": task_id}
