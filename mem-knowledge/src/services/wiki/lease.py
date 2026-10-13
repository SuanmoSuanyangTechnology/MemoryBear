"""Use the existing Redis client's token-owned lock for page Reduce."""

import logging
import threading
from contextlib import contextmanager

from ...rag.wiki.config import WikiDeferred

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

    def renew():
        while not stop.wait(LEASE_RENEW_SECONDS):
            try:
                if not lock.extend(LEASE_TTL_SECONDS, replace_ttl=True):
                    lost.set()
                    return
            except Exception:
                lost.set()
                return

    def ensure_owned():
        try:
            owned = lock.owned()
        except Exception as exc:
            raise WikiDeferred("WIKI_LEASE_UNKNOWN") from exc
        if lost.is_set() or not owned:
            raise WikiDeferred("WIKI_LEASE_LOST")

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
            # A committed result stays committed; release errors cannot overwrite it.
            logger.warning(
                "Wiki lease release failed page_id=%s error_type=%s", page_id, type(exc).__name__
            )
