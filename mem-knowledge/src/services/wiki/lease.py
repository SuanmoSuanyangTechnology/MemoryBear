"""Use the existing Redis client's token-owned lock for page Reduce."""

import logging
import threading
from contextlib import contextmanager

from ...rag.wiki.config import WikiDeferred
from ...rag.wiki.diagnostics import diagnostic_json, exception_details

logger = logging.getLogger(__name__)
LEASE_TTL_SECONDS = 120
LEASE_RENEW_SECONDS = 30
LEASE_WAIT_SECONDS = 1


@contextmanager
def page_lease(redis, kb_id, page_id):
    lock = redis.lock(
        f"wiki:reduce:{kb_id}:{page_id}",
        timeout=LEASE_TTL_SECONDS,
        blocking_timeout=LEASE_WAIT_SECONDS,
        thread_local=False,
    )
    try:
        acquired = lock.acquire()
    except Exception as exc:
        raise WikiDeferred("WIKI_REDIS_UNAVAILABLE") from exc
    if not acquired:
        raise WikiDeferred("WIKI_PAGE_BUSY")
    stop, lost = threading.Event(), threading.Event()
    renewal_failure = {}

    def renew():
        while not stop.wait(LEASE_RENEW_SECONDS):
            try:
                if not lock.extend(LEASE_TTL_SECONDS, replace_ttl=True):
                    renewal_failure.update(error_message="Redis 未确认 Wiki 页面租约续期成功")
                    lost.set()
                    return
            except Exception as exc:
                # 跨线程只传递诊断快照，保留续期失败的上游原因。
                detail = exception_details(exc)
                renewal_failure.update(error_message=detail["error_message"], renewal_error=detail)
                lost.set()
                return

    def ensure_owned():
        try:
            owned = lock.owned()
        except Exception as exc:
            raise WikiDeferred("WIKI_LEASE_UNKNOWN") from exc
        if lost.is_set() or not owned:
            raise WikiDeferred("WIKI_LEASE_LOST", diagnostics={
                **renewal_failure, "stage": "lease_renew" if lost.is_set() else "lease_check",
                "knowledge_id": str(kb_id), "page_id": str(page_id),
            })

    worker = threading.Thread(target=renew, name="wiki-page-lease", daemon=True)
    worker.start()
    try:
        yield ensure_owned
    finally:
        stop.set()
        worker.join(timeout=5)
        try:
            lock.release()
        except Exception as exc:
            # 已提交的结果保持不变，释放失败只记录完整原因。
            logger.warning(
                "Wiki 页面租约释放失败 knowledge_id=%s page_id=%s 失败详情=%s",
                kb_id, page_id, diagnostic_json(exception_details(exc)),
            )
