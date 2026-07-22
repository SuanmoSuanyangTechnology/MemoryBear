import logging
import threading
import time

import redis

from app.core.config import settings
from app.utils.redis_lock import RedisFairLock


_CLIENT_INIT_LOCK = threading.Lock()
_GRAPH_LOCK_POOL: redis.ConnectionPool | None = None
_GRAPH_LOCK_CLIENT: redis.Redis | None = None
logger = logging.getLogger(__name__)


class KnowledgeGraphLock(RedisFairLock):
    def __init__(self, knowledge_id: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._knowledge_id = str(knowledge_id)

    def acquire(self):
        started_at = time.perf_counter()
        try:
            acquired = super().acquire()
        except Exception as exc:
            logger.warning(
                "[EvidenceGraph] lock_failed"
                " kb_id=%s error_type=%s wait_ms=%d",
                self._knowledge_id,
                type(exc).__name__,
                self._elapsed_ms(started_at),
            )
            raise
        wait_ms = self._elapsed_ms(started_at)
        if acquired:
            logger.info(
                "[EvidenceGraph] lock_acquired kb_id=%s wait_ms=%d",
                self._knowledge_id,
                wait_ms,
            )
        else:
            logger.warning(
                "[EvidenceGraph] lock_timeout kb_id=%s wait_ms=%d",
                self._knowledge_id,
                wait_ms,
            )
        return acquired

    def release(self):
        if not self._locked:
            return
        valid_before_release = self.is_valid
        super().release()
        logger.info(
            "[EvidenceGraph] lock_released"
            " kb_id=%s valid_before_release=%s",
            self._knowledge_id,
            str(valid_before_release).lower(),
        )

    @staticmethod
    def _elapsed_ms(started_at: float) -> int:
        return int((time.perf_counter() - started_at) * 1000)


def build_graph_lock_redis_config() -> dict[str, object]:
    return {
        "host": settings.REDIS_HOST,
        "port": settings.REDIS_PORT,
        "db": settings.REDIS_DB,
        "password": settings.REDIS_PASSWORD,
        "decode_responses": True,
        "max_connections": 30,
        "socket_connect_timeout": 5,
        "socket_timeout": 10,
        "retry_on_timeout": True,
    }


def get_graph_lock_redis_client() -> redis.Redis:
    global _GRAPH_LOCK_POOL, _GRAPH_LOCK_CLIENT

    if _GRAPH_LOCK_CLIENT is not None:
        return _GRAPH_LOCK_CLIENT
    with _CLIENT_INIT_LOCK:
        if _GRAPH_LOCK_CLIENT is None:
            _GRAPH_LOCK_POOL = redis.ConnectionPool(
                **build_graph_lock_redis_config()
            )
            _GRAPH_LOCK_CLIENT = redis.Redis(
                connection_pool=_GRAPH_LOCK_POOL
            )
    return _GRAPH_LOCK_CLIENT


def create_knowledge_graph_lock(knowledge_id: str) -> KnowledgeGraphLock:
    return KnowledgeGraphLock(
        knowledge_id=str(knowledge_id),
        key=f"graphrag_task_{knowledge_id}",
        redis_client=get_graph_lock_redis_client(),
        expire=120,
        retry_interval=1,
        timeout=settings.KNOWLEDGE_GRAPH_LOCK_WAIT_SECONDS,
        auto_renewal=True,
    )
