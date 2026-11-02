"""Route-table hot reload (design §7.5).

A file-backed route source is polled every few seconds; a changed, valid file
atomically replaces the resolver's table. Invalid or missing files keep the
last good table and surface only through logs/metrics.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .forward import StaticTargetResolver, TargetRoute
from .metrics import gateway_route_reload_total, gateway_route_table_size

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _FileStamp:
    mtime_ns: int
    size: int
    inode: int

    @classmethod
    def read(cls, path: Path) -> _FileStamp:
        stat = path.stat()
        return cls(stat.st_mtime_ns, stat.st_size, stat.st_ino)


class RouteFileWatcher:
    """Poll one route file and swap a validated table into the resolver."""

    def __init__(
        self,
        *,
        path: str,
        resolver: StaticTargetResolver,
        loader: Callable[[str], list[TargetRoute]],
        interval_s: float = 5.0,
    ) -> None:
        self._path = Path(path)
        self._resolver = resolver
        self._loader = loader
        self._interval_s = interval_s
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        try:
            last: _FileStamp | None = _FileStamp.read(self._path)
        except OSError:
            last = None
        while True:
            await asyncio.sleep(self._interval_s)
            try:
                stamp = _FileStamp.read(self._path)
            except OSError:
                if last is not None:
                    logger.warning(
                        "gateway route file missing; keeping last table path=%s",
                        self._path,
                    )
                    gateway_route_reload_total.labels("missing").inc()
                    last = None
                continue
            if stamp == last:
                continue
            last = stamp
            try:
                routes = self._loader(str(self._path))
            except Exception as exc:  # invalid content: keep the last good table
                logger.warning(
                    "gateway route reload rejected path=%s error=%s", self._path, exc
                )
                gateway_route_reload_total.labels("rejected").inc()
                continue
            self._resolver.replace(routes)
            gateway_route_table_size.set(len(routes))
            gateway_route_reload_total.labels("applied").inc()
            logger.info(
                "gateway route table reloaded path=%s routes=%d",
                self._path,
                len(routes),
            )


__all__ = ["RouteFileWatcher"]
