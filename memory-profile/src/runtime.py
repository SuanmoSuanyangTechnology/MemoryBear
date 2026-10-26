"""Process-level orchestration for profile infrastructure managers."""

from __future__ import annotations

import asyncio
import os
import socket
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from src.common.normalization import parse_int
from src.common.observability.http_metrics import HttpMetrics
from src.common.observability.runtime_metrics import (
    collect_process_metrics,
    collect_python_metrics,
    collect_tracemalloc_metrics,
    current_rss_bytes,
    utc_timestamp,
)
from src.config import ProfileSettings
from src.infrastructure.elasticsearch import ElasticsearchManager
from src.infrastructure.model_service import ModelServiceManager
from src.infrastructure.neo4j import Neo4jManager
from src.infrastructure.postgres import PostgresManager
from src.infrastructure.redis import RedisManager
from src.infrastructure.task_bridge import TaskBridgeManager

HealthCheck = Callable[[], Awaitable[bool]]


class ProfileRuntime:
    """Compose lazy process resources without owning client construction details."""

    def __init__(self, settings: ProfileSettings) -> None:
        self.settings = settings
        self.postgres = PostgresManager(settings)
        self.neo4j = Neo4jManager(settings)
        self.redis = RedisManager(settings)
        self.elasticsearch = ElasticsearchManager(settings)
        # 生成类用例专用；只读端点不会触碰它，因此它不参与 readiness 探测。
        self.model_service = ModelServiceManager(settings)
        # 缓存生成投递到 memory-jobs 的 worker；本进程只做 producer + 结果等待。
        self.task_bridge = TaskBridgeManager(settings)
        self.http_metrics = HttpMetrics()
        self.started_at_monotonic = time.monotonic()
        self.started_at = datetime.now(UTC)
        self.instance_name = socket.gethostname() or "unknown"
        self.baseline_rss_bytes = current_rss_bytes()
        self._tracemalloc_cache: tuple[float, int, dict[str, Any]] | None = None

    async def readiness(self) -> dict[str, str]:
        timeout = self.settings.health_probe_timeout_seconds
        checks: dict[str, HealthCheck] = {
            "postgres": self.postgres.ping,
            "neo4j": self.neo4j.ping,
            "redis": self.redis.ping,
            "elasticsearch": self.elasticsearch.ping,
        }

        async def probe(name: str, check: HealthCheck) -> tuple[str, str]:
            try:
                healthy = await asyncio.wait_for(check(), timeout=timeout)
                return name, "up" if healthy else "down"
            except Exception:
                return name, "down"

        return dict(await asyncio.gather(*(probe(name, check) for name, check in checks.items())))

    def dependency_snapshots(self) -> dict[str, dict[str, Any]]:
        """Read in-memory pool counters for every dependency (no network I/O)."""

        managers = {
            "postgres": self.postgres,
            "neo4j": self.neo4j,
            "redis": self.redis,
            "elasticsearch": self.elasticsearch,
        }
        snapshots: dict[str, dict[str, Any]] = {}
        for name, manager in managers.items():
            entry: dict[str, Any] = {"initialized": manager.initialized}
            pool = manager.pool_snapshot()
            if pool is not None:
                entry["pool"] = pool
            snapshots[name] = entry
        return snapshots

    def runtime_warnings(
        self,
        process: dict[str, Any],
        dependencies: dict[str, dict[str, Any]],
    ) -> list[str]:
        """Derive actionable states from counters that are already in memory."""

        warnings: list[str] = []

        postgres = dependencies.get("postgres", {}).get("pool")
        if isinstance(postgres, dict):
            capacity = parse_int(postgres.get("capacity"))
            checked_out = parse_int(postgres.get("checked_out"))
            if capacity and checked_out is not None and checked_out >= capacity:
                warnings.append("postgres_pool_saturated")

        neo4j = dependencies.get("neo4j", {}).get("pool")
        if isinstance(neo4j, dict):
            max_size = parse_int(neo4j.get("max_size"))
            in_use = parse_int(neo4j.get("in_use"))
            if max_size and in_use is not None and in_use >= max_size:
                warnings.append("neo4j_pool_saturated")

        redis = dependencies.get("redis", {}).get("pool")
        if isinstance(redis, dict):
            max_connections = parse_int(redis.get("max_connections"))
            in_use = parse_int(redis.get("in_use"))
            if max_connections and in_use is not None and in_use >= max_connections:
                warnings.append("redis_pool_saturated")

        elasticsearch = dependencies.get("elasticsearch", {}).get("pool")
        if isinstance(elasticsearch, dict):
            nodes = parse_int(elasticsearch.get("nodes"))
            alive = parse_int(elasticsearch.get("alive"))
            if nodes and alive == 0:
                warnings.append("elasticsearch_no_alive_nodes")

        rss = process.get("rss_bytes")
        if rss is None:
            warnings.append("process_metrics_unavailable")
        threshold = self.settings.memory_rss_warn_bytes
        if threshold > 0 and isinstance(rss, int) and rss >= threshold:
            warnings.append("rss_threshold_exceeded")

        return warnings

    def _tracemalloc_metrics(self, top: int) -> dict[str, Any]:
        """Memoize the expensive top-allocation snapshot for repeated scrapes."""

        frames = self.settings.memory_tracemalloc_frames
        if top <= 0:
            return collect_tracemalloc_metrics(0, frames)
        now = time.monotonic()
        cached = self._tracemalloc_cache
        if cached is not None and cached[1] == top and now - cached[0] < 10.0:
            return cached[2]
        result = collect_tracemalloc_metrics(top, frames)
        self._tracemalloc_cache = (now, top, result)
        return result

    async def metrics_snapshot(self, top: int = 0) -> dict[str, Any]:
        """Assemble the runtime-monitor payload without calling any dependency."""

        process = collect_process_metrics(self.baseline_rss_bytes)
        python = collect_python_metrics()
        python["tracemalloc"] = self._tracemalloc_metrics(top)
        dependencies = self.dependency_snapshots()
        warnings = self.runtime_warnings(process, dependencies)
        return {
            "status": "degraded" if warnings else "ok",
            "timestamp": utc_timestamp(),
            "started_at": self.started_at.isoformat(timespec="milliseconds").replace(
                "+00:00", "Z"
            ),
            "instance": self.instance_name,
            "pid": os.getpid(),
            "uptime_seconds": round(time.monotonic() - self.started_at_monotonic, 3),
            "warnings": warnings,
            "process": process,
            "python": python,
            "http": self.http_metrics.snapshot(),
            "dependencies": dependencies,
        }

    async def aclose(self) -> None:
        await asyncio.gather(
            self.postgres.aclose(),
            self.neo4j.aclose(),
            self.redis.aclose(),
            self.elasticsearch.aclose(),
            self.model_service.aclose(),
            self.task_bridge.aclose(),
        )
