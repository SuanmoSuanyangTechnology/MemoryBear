import threading

import redis

from app.core.config import settings
from app.utils.redis_lock import RedisFairLock


_CLIENT_INIT_LOCK = threading.Lock()
_GRAPH_LOCK_POOL: redis.ConnectionPool | None = None
_GRAPH_LOCK_CLIENT: redis.Redis | None = None


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


def create_knowledge_graph_lock(knowledge_id: str) -> RedisFairLock:
    return RedisFairLock(
        key=f"graphrag_task_{knowledge_id}",
        redis_client=get_graph_lock_redis_client(),
        expire=120,
        retry_interval=1,
        timeout=settings.KNOWLEDGE_GRAPH_LOCK_WAIT_SECONDS,
        auto_renewal=True,
    )
