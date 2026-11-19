"""异步 FAQ 导入：结构入口校验，逐条准备索引，再提交成功记录。"""

import asyncio
import json
import logging
import uuid
from contextlib import suppress

from redis.exceptions import LockNotOwnedError, RedisError
from sqlalchemy import select
from sqlalchemy.exc import DataError, IntegrityError, InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as DatabaseTimeoutError

from ..errors import KnowledgeError
from ..models.owned import Knowledge
from ..rag.faq.index import delete_projection, is_indexed, write_projection
from ..rag.wiki.diagnostics import diagnostic_json, exception_details
from ..repositories.wiki_faq import import_jobs as jobs
from ..tasks.dispatch import TaskDispatcher
from .knowledge_file_storage import KnowledgeFileStorage
from .wiki.runtime import transaction
from .wiki_faq_retrieval import _target_for_sync

logger = logging.getLogger(__name__)
FAQ_IMPORT_TASK = "mem_knowledge.tasks.import_faqs"
SUBMISSION_LOCK_TTL_SECONDS = 30
SUBMISSION_LOCK_RENEW_SECONDS = 10
INDEX_BATCH_SIZE = 3
INDEX_CONCURRENCY = 3


async def db_call(runtime, operation, *args):
    return await asyncio.to_thread(transaction, runtime, operation, *args)


async def _discard_unregistered_payload(
    storage: KnowledgeFileStorage, task_id: uuid.UUID, key: str
) -> None:
    try:
        await storage.delete(key)
    except Exception as exc:
        logger.exception(
            "FAQ 导入载荷清理失败 task_id=%s stage=discard key=%s details=%s",
            task_id,
            key,
            diagnostic_json(exception_details(exc)),
        )


async def _renew_submission_lock(lock, task_id):
    while True:
        await asyncio.sleep(SUBMISSION_LOCK_RENEW_SECONDS)
        try:
            await lock.extend(SUBMISSION_LOCK_TTL_SECONDS, replace_ttl=True)
        except LockNotOwnedError:
            logger.warning("FAQ 导入提交锁已失效 task_id=%s", task_id, exc_info=True)
            return
        except RedisError:
            logger.warning("FAQ 导入提交锁续期失败 task_id=%s", task_id, exc_info=True)


async def submit(runtime, scope, actor_id, payload):
    task_id = uuid.uuid4()
    lock_key = (
        f"kb:faq-import:submit:{runtime.settings.db_name}:"
        f"{scope['tenant_id']}:{scope['workspace_id']}:{scope['kb_id']}"
    )
    try:
        redis = await runtime.redis.client()
        lock = redis.lock(lock_key, timeout=SUBMISSION_LOCK_TTL_SECONDS)
        acquired = await lock.acquire(blocking=False, token=str(task_id))
    except RedisError as exc:
        logger.exception("FAQ 导入申请提交锁失败 task_id=%s kb_id=%s", task_id, scope["kb_id"])
        raise KnowledgeError.from_code("KB_HTTP_UNAVAILABLE") from exc
    if not acquired:
        raise KnowledgeError.from_code("KB_FAQ_IMPORT_BUSY", status_code=409)

    renewal = asyncio.create_task(_renew_submission_lock(lock, task_id))
    try:
        return await _submit_with_lock(runtime, scope, actor_id, payload, task_id, lock)
    finally:
        renewal.cancel()
        with suppress(asyncio.CancelledError):
            await renewal
        try:
            await lock.release()
        except RedisError:
            logger.warning(
                "FAQ 导入释放提交锁失败 task_id=%s kb_id=%s", task_id, scope["kb_id"], exc_info=True
            )


async def _submit_with_lock(runtime, scope, actor_id, payload, task_id, lock):
    timeout_seconds = runtime.settings.kb_faq_import_pending_timeout_seconds
    active, expired = await db_call(runtime, jobs.prepare_submission, scope, timeout_seconds)
    for expired_job in expired:
        await cleanup(runtime, expired_job)
    if active is not None:
        raise KnowledgeError.from_code("KB_FAQ_IMPORT_BUSY", status_code=409)
    key = f"kb/{scope['kb_id']}/faq-import/{task_id}.json"
    storage = KnowledgeFileStorage(runtime.storage)
    content = await asyncio.to_thread(
        lambda: json.dumps(payload.model_dump(), ensure_ascii=True).encode("utf-8")
    )
    try:
        await storage.upload(key, content, "application/json")
    except Exception as exc:
        logger.exception(
            "FAQ 导入保存失败 task_id=%s stage=save details=%s",
            task_id,
            diagnostic_json(exception_details(exc)),
        )
        await _discard_unregistered_payload(storage, task_id, key)
        raise KnowledgeError.from_code("KB_HTTP_UNAVAILABLE") from exc
    try:
        owns_lock = await lock.owned()
    except RedisError as exc:
        logger.exception("FAQ 导入检查提交锁失败 task_id=%s", task_id)
        await _discard_unregistered_payload(storage, task_id, key)
        raise KnowledgeError.from_code("KB_HTTP_UNAVAILABLE") from exc
    if not owns_lock:
        logger.warning("FAQ 导入提交锁已失效 task_id=%s", task_id)
        await _discard_unregistered_payload(storage, task_id, key)
        raise KnowledgeError.from_code("KB_HTTP_UNAVAILABLE")
    expired = []
    try:
        # 上传期间可能已有其他请求占位，以写入时的知识库行锁检查为准。
        expired = await db_call(
            runtime,
            jobs.create,
            scope,
            actor_id,
            task_id,
            payload.mode,
            key,
            len(payload.items),
            timeout_seconds,
        )
    except (KnowledgeError, DataError, IntegrityError):
        await _discard_unregistered_payload(storage, task_id, key)
        raise
    except Exception:
        logger.exception("FAQ 导入任务登记失败 task_id=%s stage=register key=%s", task_id, key)
        # 提交回执丢失时可能已经入库，核实后才能继续派发。
        registered = False
        try:
            registered = await db_call(runtime, jobs.load, task_id) is not None
        except Exception:
            logger.exception("FAQ 导入任务登记状态查询失败 task_id=%s", task_id)
        else:
            if not registered:
                await _discard_unregistered_payload(storage, task_id, key)
        if not registered:
            raise
    try:
        await TaskDispatcher(runtime=runtime).send(
            FAQ_IMPORT_TASK,
            kwargs={"task_id": str(task_id)},
            queue="qa_import",
            task_id=str(task_id),
            connect_before_publish=True,
        )
    except KnowledgeError:
        logger.exception("FAQ 导入派发异常 task_id=%s stage=dispatch", task_id)
        job = await db_call(runtime, jobs.cancel_pending_dispatch, task_id)
        if job is None:
            # 消息已被领取或执行完成，返回任务句柄供调用方查询真实结果。
            return {"task_id": str(task_id), "status": "pending"}
        await cleanup(runtime, job)
        raise
    finally:
        for expired_job in expired:
            await cleanup(runtime, expired_job)
    return {"task_id": str(task_id), "status": "pending"}


async def active_task_id(runtime, scope) -> uuid.UUID | None:
    return await db_call(
        runtime, jobs.active_task_id, scope, runtime.settings.kb_faq_import_pending_timeout_seconds
    )


async def progress(runtime, scope, task_id):
    return await db_call(
        runtime,
        jobs.progress,
        scope,
        task_id,
        runtime.settings.kb_faq_import_pending_timeout_seconds,
    )


def content_error(item):
    question, answer = item["standard_question"].strip(), item["standard_answer"].strip()
    if not question or not answer:
        return "问题和答案均为必填项，不能仅包含空白。"
    try:
        question.encode("utf-8")
        answer.encode("utf-8")
        length = len(question.encode("utf-16-le")) // 2
    except UnicodeEncodeError:
        return "问答包含无效字符，请修正后重新导入。"
    if "\x00" in question or "\x00" in answer:
        return "问答包含空字符，无法写入。"
    if length > 200:
        return "问题长度超过 200 个字符（与前端 UTF-16 计数一致）。"
    return None


def candidate_id(task_id, index):
    return str(uuid.uuid5(uuid.UUID(str(task_id)), f"faq:{index}"))


async def prepare_one(runtime, target, task_id, index, item, semaphore):
    error = content_error(item)
    if error:
        return None, jobs.entry_failure(index, item, "validation", error)
    faq_id = candidate_id(task_id, index)
    row = {
        "id": faq_id,
        "content_revision": 1,
        "standard_question": item["standard_question"].strip(),
        "answers": [item["standard_answer"].strip()],
        "similar_questions": [],
        "negative_questions": [],
        "answer_strategy": "all",
    }
    try:
        async with semaphore:
            client = await runtime.elasticsearch.client()
            await write_projection(runtime, client, target, row)
            if not await is_indexed(client, target, row):
                raise RuntimeError("FAQ_IMPORT_INDEX_NOT_READY")
        return {"index": index, "id": faq_id}, None
    except Exception as exc:
        logger.exception(
            "FAQ 导入索引失败 task_id=%s faq_id=%s stage=indexing details=%s",
            task_id,
            faq_id,
            diagnostic_json(exception_details(exc)),
        )
        reason = "向量索引准备失败，本条问答未导入。"
        if (
            isinstance(exc, TimeoutError)
            or "timeout" in type(exc).__name__.lower()
            or getattr(exc, "http_status", None) == 504
            or getattr(exc, "remote_code", None) == 10003
        ):
            reason = "向量模型或索引服务超时，本条问答未导入。"
        elif isinstance(exc, KnowledgeError):
            from ..i18n import translate

            reason = translate(exc.code, "zh", exc.params)
        return None, jobs.entry_failure(index, item, "indexing", reason)


def embedding_snapshot(session, scope):
    jobs.validate_scope(session, scope)
    kb = session.scalar(select(Knowledge).where(Knowledge.id == scope["kb_id"]))
    return {"embedding_id": kb.embedding_id}


async def cleanup(runtime, job, *, retry_on_error=False):
    if not job or job["status"] not in jobs.TERMINAL:
        return
    scope = {key: job[key] for key in ("kb_id", "workspace_id", "tenant_id")}
    try:
        cleanup_ids = set(job["cleanup_ids"])
        cleanup_ids.update(
            candidate_id(job["id"], e["item_index"] - 1) for e in job["failed_entries"]
        )
        if cleanup_ids:
            client = await runtime.elasticsearch.client()
            for faq_id in cleanup_ids:
                await delete_projection(client, scope, faq_id)
        await KnowledgeFileStorage(runtime.storage).delete(job["payload_key"])
        await db_call(runtime, jobs.cleaned, job["id"])
    except Exception as exc:
        logger.exception(
            "FAQ 导入临时数据清理失败 task_id=%s stage=cleanup details=%s",
            job["id"],
            diagnostic_json(exception_details(exc)),
        )
        if retry_on_error:
            raise ImportCleanupRetry(str(job["id"])) from exc


class ImportCleanupRetry(Exception):
    """业务已结束，重投只执行幂等清理。"""


async def execute(runtime, task_id):
    task_id, token = uuid.UUID(str(task_id)), uuid.uuid4()
    job = await db_call(
        runtime,
        jobs.claim,
        task_id,
        token,
        runtime.settings.kb_task_time_limit_seconds,
        runtime.settings.kb_faq_import_pending_timeout_seconds,
    )
    if job is None:
        await cleanup(runtime, await db_call(runtime, jobs.load, task_id), retry_on_error=True)
        return False
    if job.get("busy"):
        return True
    scope = {key: job[key] for key in ("kb_id", "workspace_id", "tenant_id")}
    try:
        raw = await KnowledgeFileStorage(runtime.storage).download(job["payload_key"])
        payload = await asyncio.to_thread(json.loads, raw)
        items = payload["items"]
        model = await db_call(runtime, embedding_snapshot, scope)
        target = await _target_for_sync(runtime, scope, model)
        # 配置切换后重新准备当前模型投影；同一 FAQ ID 可安全重做。
        start = job["processed_count"] if job["model_key"] == target.model_key else 0
        candidates = list(job["candidates"]) if start else []
        failures = list(job["failed_entries"]) if start else []
        await db_call(
            runtime,
            jobs.checkpoint,
            task_id,
            token,
            candidates,
            failures,
            start,
            target.model_key,
            "indexing",
        )
        semaphore = asyncio.Semaphore(INDEX_CONCURRENCY)
        for offset in range(start, len(items), INDEX_BATCH_SIZE):
            batch = items[offset : offset + INDEX_BATCH_SIZE]
            result = await asyncio.gather(
                *[
                    prepare_one(runtime, target, task_id, offset + i, item, semaphore)
                    for i, item in enumerate(batch)
                ]
            )
            candidates.extend(c for c, _ in result if c)
            failures.extend(f for _, f in result if f)
            await db_call(
                runtime,
                jobs.checkpoint,
                task_id,
                token,
                candidates,
                failures,
                offset + len(batch),
                target.model_key,
                "indexing",
            )
        latest = await db_call(runtime, embedding_snapshot, scope)
        latest_target = await _target_for_sync(runtime, scope, latest)
        if latest_target.model_key != target.model_key:
            await db_call(runtime, jobs.release_for_reindex, task_id, token)
            return True
        await db_call(
            runtime,
            jobs.checkpoint,
            task_id,
            token,
            candidates,
            failures,
            len(items),
            target.model_key,
            "writing",
        )
        finished = await db_call(
            runtime, jobs.commit_faqs, task_id, token, items, model["embedding_id"]
        )
    except jobs.ImportModelChanged:
        await db_call(runtime, jobs.release_for_reindex, task_id, token)
        return True
    except (InterfaceError, OperationalError, DatabaseTimeoutError):
        logger.exception("FAQ 导入数据库暂不可用 task_id=%s stage=execute", task_id)
        await db_call(runtime, jobs.release_after_db_error, task_id, token)
        raise
    except Exception as exc:
        logger.exception(
            "FAQ 导入任务失败 task_id=%s stage=execute details=%s",
            task_id,
            diagnostic_json(exception_details(exc)),
        )
        # 若 commit 成功而回执丢失，fail 会保留已持久化终态。
        finished = await db_call(
            runtime, jobs.fail, task_id, token, "导入执行失败，本批未写入；请检查任务日志。"
        )
    await cleanup(runtime, finished, retry_on_error=True)
    return False
