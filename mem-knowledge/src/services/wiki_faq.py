"""FAQ 管理与消息交接；同步 Session 只在隔离线程内短暂持有。"""

import asyncio
import logging

from ..errors import KnowledgeError
from ..rag.faq.contracts import FAQ_GENERATE_TASK, FAQ_QUEUE
from ..rag.wiki.config import WikiError
from ..rag.wiki.diagnostics import diagnostic_json, exception_details
from ..repositories.wiki_faq import jobs, read, write
from ..tasks.dispatch import TaskDispatcher
from .wiki.runtime import transaction

logger = logging.getLogger(__name__)


async def run(runtime, operation, scope, *args, **kwargs):
    synchronize_index = kwargs.pop("_synchronize_index", True)
    try:
        result = await asyncio.to_thread(transaction, runtime, operation, scope, *args, **kwargs)
    except WikiError as exc:
        raise KnowledgeError.from_code("KB_RESOURCE_NOT_FOUND") from exc
    if synchronize_index and operation in {
        write.create,
        write.edit,
        write.set_enabled,
        write.approve,
    }:
        from .wiki_faq_retrieval import synchronize_results

        await synchronize_results(runtime, scope, [result])
    if synchronize_index and operation is write.delete:
        from .wiki_faq_retrieval import cleanup_deleted_projection

        await cleanup_deleted_projection(runtime, scope, result["id"])
    return result


def _publish(runtime, delivery):
    try:
        dispatcher = getattr(runtime, "task_dispatcher", None) or TaskDispatcher(runtime=runtime)
        dispatcher.send_sync(
            FAQ_GENERATE_TASK,
            kwargs={"kb_id": delivery["kb_id"], "job_id": delivery["job_id"]},
            queue=FAQ_QUEUE,
            task_id=delivery["task_id"],
        )
        transaction(runtime, jobs.record_dispatch, delivery, succeeded=True)
        return True
    except Exception as exc:
        logger.exception(
            "FAQ 任务投递失败 job_id=%s task_id=%s stage=dispatch details=%s",
            delivery["job_id"],
            delivery["task_id"],
            diagnostic_json(exception_details(exc)),
        )
        try:
            transaction(runtime, jobs.record_dispatch, delivery, succeeded=False)
        except Exception as record_exc:
            logger.error(
                "FAQ 投递结果持久化失败 job_id=%s stage=record_dispatch details=%s",
                delivery["job_id"],
                diagnostic_json(exception_details(record_exc)),
            )
        return False


def publish_pending(runtime, kb_id, page_id):
    """与 Wiki 成功结果隔离；空 Reduce 重投仍可补发未确认 job。"""
    try:
        deliveries = transaction(runtime, jobs.pending_deliveries, kb_id, page_id)
        for delivery in deliveries:
            _publish(runtime, delivery)
    except Exception as exc:
        logger.exception(
            "FAQ 待投递任务读取失败 kb_id=%s page_id=%s stage=load_dispatch details=%s",
            kb_id,
            page_id,
            diagnostic_json(exception_details(exc)),
        )


async def retry_job(runtime, scope, job_id, expected_row_version):
    delivery = await run(runtime, jobs.retry, scope, job_id, expected_row_version)
    await asyncio.to_thread(_publish, runtime, delivery)
    return await run(runtime, jobs.dispatch_result, scope, job_id)


async def batch(runtime, scope, actor_id, items, *, action, is_enabled=None):
    await run(runtime, read.validate_batch, scope, [item["faq_id"] for item in items])
    results = []
    for item in items:
        payload = {key: value for key, value in item.items() if key != "faq_id"}
        if action == "enabled":
            payload["is_enabled"] = is_enabled
        operation = write.approve if action == "approve" else write.set_enabled
        try:
            value = await run(
                runtime,
                operation,
                scope,
                item["faq_id"],
                actor_id,
                payload,
                _synchronize_index=False,
            )
            results.append(
                {
                    "faq_id": str(item["faq_id"]),
                    "result": "succeeded" if value["changed"] else "skipped",
                    "faq": value["faq"],
                    "error_code": None,
                }
            )
        except KnowledgeError as exc:
            results.append(
                {"faq_id": str(item["faq_id"]), "result": "failed", "error_code": exc.code}
            )
    from .wiki_faq_retrieval import synchronize_results

    await synchronize_results(runtime, scope, [r for r in results if "faq" in r])
    return {
        "requested_count": len(items),
        **{
            f"{status}_count": sum(r["result"] == status for r in results)
            for status in ("succeeded", "skipped", "failed")
        },
        "results": results,
    }
