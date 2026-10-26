"""Process-level composition of the synchronous infrastructure clients."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from src.bootstrap import get_settings
from src.config import JobsSettings
from src.infrastructure.elasticsearch import ElasticsearchManager
from src.infrastructure.model_service import ModelServiceManager
from src.infrastructure.neo4j import Neo4jManager
from src.infrastructure.postgres import PostgresManager
from src.infrastructure.redis import RedisManager

HealthCheck = Callable[[], bool]

logger = logging.getLogger(__name__)


class JobsRuntime:
    """Compose lazy, blocking clients without owning their construction details.

    Nothing connects at import or construction time. Connections are opened on
    first use inside the process that actually serves the task, which is what
    makes the runtime safe for Celery's prefork pool.
    """

    def __init__(self, settings: JobsSettings | None = None) -> None:
        self.settings = settings or get_settings()
        self.postgres = PostgresManager(self.settings)
        self.redis = RedisManager(self.settings)
        self.elasticsearch = ElasticsearchManager(self.settings)
        self.neo4j = Neo4jManager(self.settings)
        #: 仅生成类任务使用；纯读/写任务不会触碰它，所以不参与依赖探测。
        self.model_service = ModelServiceManager(self.settings)

    def dependency_health(self) -> dict[str, str]:
        """Ping every dependency; never raises, reports ``up``/``down``."""

        checks: dict[str, HealthCheck] = {
            "postgres": self.postgres.ping,
            "redis": self.redis.ping,
            "elasticsearch": self.elasticsearch.ping,
            "neo4j": self.neo4j.ping,
        }
        health: dict[str, str] = {}
        for name, check in checks.items():
            try:
                health[name] = "up" if check() else "down"
            except Exception as exc:
                logger.warning(
                    "Dependency probe failed dependency=%s error=%s",
                    name,
                    type(exc).__name__,
                    extra={
                        "event": "dependency_probe_failed",
                        "exception_type": type(exc).__name__,
                    },
                )
                health[name] = "down"
        return health

    def verify_dependencies(self) -> dict[str, str]:
        """Probe dependencies at worker startup and fail fast when any is down."""

        health = self.dependency_health()
        down = sorted(name for name, status in health.items() if status != "up")
        if down:
            raise RuntimeError(f"Unhealthy dependencies: {', '.join(down)}")
        return health

    def pool_snapshots(self) -> dict[str, dict[str, Any]]:
        """Read in-memory pool counters for every dependency (no network I/O)."""

        managers = {
            "postgres": self.postgres,
            "redis": self.redis,
            "elasticsearch": self.elasticsearch,
            "neo4j": self.neo4j,
        }
        snapshots: dict[str, dict[str, Any]] = {}
        for name, manager in managers.items():
            entry: dict[str, Any] = {"initialized": manager.initialized}
            pool = manager.pool_snapshot()
            if pool is not None:
                entry["pool"] = pool
            snapshots[name] = entry
        return snapshots

    def reset_after_fork(self) -> None:
        """Replace every client in a forked child before it serves tasks."""

        self.postgres.reset_after_fork()
        self.redis.reset_after_fork()
        self.elasticsearch.reset_after_fork()
        self.neo4j.reset_after_fork()
        self.model_service.reset_after_fork()

    def close(self) -> None:
        for name, manager in (
            ("postgres", self.postgres),
            ("redis", self.redis),
            ("elasticsearch", self.elasticsearch),
            ("neo4j", self.neo4j),
            ("model_service", self.model_service),
        ):
            try:
                manager.close()
            except Exception as exc:
                logger.warning(
                    "Failed to close dependency dependency=%s error=%s",
                    name,
                    type(exc).__name__,
                    extra={
                        "event": "dependency_close_failed",
                        "exception_type": type(exc).__name__,
                    },
                )


_runtime: JobsRuntime | None = None
_runtime_lock = threading.Lock()


def get_runtime() -> JobsRuntime:
    """Return the process-wide runtime, creating it on first use."""

    global _runtime
    if _runtime is None:
        with _runtime_lock:
            if _runtime is None:
                _runtime = JobsRuntime()
    return _runtime


def set_runtime(runtime: JobsRuntime | None) -> None:
    """Replace the process runtime (tests and startup wiring)."""

    global _runtime
    with _runtime_lock:
        _runtime = runtime


def close_runtime() -> None:
    """Close and forget the process runtime."""

    runtime = get_runtime()
    set_runtime(None)
    runtime.close()
