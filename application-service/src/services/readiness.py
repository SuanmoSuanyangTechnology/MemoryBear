"""Sample required dependencies independently of the request connection pools."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from random import uniform

import asyncpg
from redis.asyncio import Redis

from ..api.schemas.health import ComponentHealth
from ..config import AppServiceSettings

_PROBE_INTERVAL = 5.0
_PROBE_JITTER = 1.0
_FAILURES_TO_UNREADY = 3
_SUCCESSES_TO_READY = 2


async def _probe(check: Callable[[], Awaitable[bool]], timeout_seconds: float) -> ComponentHealth:
    started = time.monotonic()
    try:
        async with asyncio.timeout(timeout_seconds):
            available = await check()
        status = "up" if available else "down"
        error_type = None if available else "Unavailable"
    except TimeoutError:
        status, error_type = "timeout", "TimeoutError"
    except Exception as exc:
        status, error_type = "down", type(exc).__name__
    return ComponentHealth(
        status=status,
        latency_ms=int((time.monotonic() - started) * 1000),
        error_type=error_type,
    )


class ReadinessMonitor:
    def __init__(self, settings: AppServiceSettings) -> None:
        self._settings = settings
        self._timeout = settings.application_service_health_probe_timeout_seconds
        self._max_age = 3 * (_PROBE_INTERVAL + _PROBE_JITTER + self._timeout)
        self._task: asyncio.Task[None] | None = None
        self._database: asyncpg.Connection | None = None
        self._ready = False
        self._last_sample: float | None = None
        self.checked_at_ms: int | None = None
        self.components: dict[str, ComponentHealth] = {}

    @property
    def is_ready(self) -> bool:
        return (
            self._task is not None
            and not self._task.done()
            and self._ready
            and self._last_sample is not None
            and time.monotonic() - self._last_sample < self._max_age
        )

    def start(self) -> None:
        self._ready = False
        self._last_sample = None
        self.checked_at_ms = None
        self.components = {}
        self._task = asyncio.create_task(self._run(), name="dependency-readiness")

    async def aclose(self) -> None:
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def _ping_database(self) -> bool:
        if self._database is None:
            self._database = await asyncpg.connect(
                host=self._settings.db_host,
                port=self._settings.db_port,
                user=self._settings.db_user,
                password=self._settings.db_password.get_secret_value(),
                database=self._settings.db_name,
                timeout=self._timeout,
            )
        try:
            return await self._database.fetchval("SELECT 1") == 1
        except (Exception, asyncio.CancelledError):
            self._database.terminate()
            self._database = None
            raise

    async def _run(self) -> None:
        redis = Redis.from_url(
            self._settings.redis_url,
            single_connection_client=True,
            max_connections=1,
            socket_connect_timeout=self._timeout,
            socket_timeout=self._timeout,
        )
        successes = failures = 0
        try:
            while True:
                database, cache = await asyncio.gather(
                    _probe(self._ping_database, self._timeout),
                    _probe(redis.ping, self._timeout),
                )
                if database.status == cache.status == "up":
                    successes += 1
                    failures = 0
                    if self._last_sample is None or successes >= _SUCCESSES_TO_READY:
                        self._ready = True
                else:
                    failures += 1
                    successes = 0
                    if failures >= _FAILURES_TO_UNREADY:
                        self._ready = False
                self.components = {"database": database, "redis": cache}
                self.checked_at_ms = time.time_ns() // 1_000_000
                self._last_sample = time.monotonic()
                await asyncio.sleep(_PROBE_INTERVAL + uniform(0, _PROBE_JITTER))
        finally:
            if self._database is not None:
                self._database.terminate()
                self._database = None
            await redis.aclose()
