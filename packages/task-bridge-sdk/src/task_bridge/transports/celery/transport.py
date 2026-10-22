"""Celery implementation of the task transport contract.

This module owns everything Celery-specific: the bare producer app, queue
routing, JSON payload validation, the Redis result fast path and result
pub/sub wake-ups. The generic bridge only sees
:class:`~task_bridge.core.transport.TaskSubmission`, ``TaskStatus`` and the
lifecycle methods.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import uuid4

from celery import Celery

from task_bridge.core.errors import (
    BridgeConfigurationError,
    BridgeError,
    InvalidTaskPayloadError,
    TaskPublishError,
    TaskResultUnavailableError,
)
from task_bridge.core.status import TaskStatus
from task_bridge.core.transport import (
    TaskSubmission,
    TaskWatch,
    ThreadedTaskTransport,
)
from task_bridge.transports.celery.config import CeleryConfig
from task_bridge.transports.celery.redis_results import (
    RedisResultNotifier,
    RedisResultReader,
)

logger = logging.getLogger(__name__)


class _EventWatch(TaskWatch):
    """Wraps one notifier registration behind the transport-neutral watch API."""

    def __init__(
        self,
        notifier: RedisResultNotifier,
        task_id: str,
        event: asyncio.Event,
    ) -> None:
        self._notifier = notifier
        self._task_id = task_id
        self._event = event

    def clear(self) -> None:
        self._event.clear()

    async def wait(self, timeout: float) -> None:
        try:
            await asyncio.wait_for(self._event.wait(), timeout=timeout)
        except TimeoutError:
            pass

    async def aclose(self) -> None:
        await self._notifier.unregister(self._task_id, self._event)


class CeleryTransport(ThreadedTaskTransport):
    """Publish to Celery by task name and read results from its backend."""

    def __init__(
        self,
        config: CeleryConfig | None = None,
        *,
        app: Celery | None = None,
        validate_payload: bool | None = None,
        executor: ThreadPoolExecutor | None = None,
        executor_workers: int | None = None,
    ) -> None:
        if config is not None and app is not None:
            raise BridgeConfigurationError("pass either config or app, not both")
        if config is None and app is None:
            config = CeleryConfig.from_env()
        if executor_workers is None and config is not None:
            executor_workers = config.executor_workers
        super().__init__(executor=executor, executor_workers=executor_workers)

        self._config = config
        self._app = app
        self._owns_app = app is None
        if validate_payload is None:
            validate_payload = config.validate_payload if config is not None else True
        self._payload_validation_enabled = validate_payload

        self._app_lock = threading.Lock()
        # Celery's ``app.backend`` is lazy and unlocked: a cold burst of
        # concurrent reads would otherwise build one backend (and connection
        # pool) per worker thread. Resolve it once here instead.
        self._backend: Any | None = None
        self._backend_lock = threading.Lock()
        # Async Redis result path (optional): no worker thread per read.
        self._reader: RedisResultReader | None = None
        self._reader_attempted = False
        self._reader_lock = threading.Lock()

    # ── Configuration / app ─────────────────────────────────────────────

    @property
    def config(self) -> CeleryConfig | None:
        return self._config

    @property
    def app(self) -> Celery:
        """The producer app, created on first access."""

        app = self._app
        if app is not None:
            return app
        with self._app_lock:
            if self._app is None:
                self._app = self._config.build_app()
            return self._app

    def _resolve_backend(self) -> Any:
        backend = self._backend
        if backend is None:
            with self._backend_lock:
                if self._backend is None:
                    self._backend = self.app.backend
                backend = self._backend
        return backend

    def _get_result_reader(self) -> RedisResultReader | None:
        """Return the async Redis reader, or ``None`` to use the thread path."""

        if self._config is not None and not self._config.prefer_async_redis_results:
            return None
        if self._reader_attempted:
            return self._reader
        with self._reader_lock:
            if not self._reader_attempted:
                self._reader_attempted = True
                config = self._config
                try:
                    self._reader = RedisResultReader.create(
                        self._resolve_backend(),
                        self.app.conf.result_backend,
                        socket_timeout=(
                            config.redis_socket_timeout
                            if config is not None
                            else self.app.conf.redis_socket_timeout
                        ),
                        socket_connect_timeout=(
                            config.redis_socket_connect_timeout
                            if config is not None
                            else self.app.conf.redis_socket_connect_timeout
                        ),
                        max_connections=(
                            config.async_redis_max_connections if config is not None else None
                        ),
                    )
                except Exception:  # noqa: BLE001 - fall back to the backend call
                    logger.debug("async Redis result path unavailable", exc_info=True)
                    self._reader = None
        return self._reader

    def _get_result_notifier(self) -> RedisResultNotifier | None:
        """Return the pub/sub notifier, or ``None`` to keep plain polling."""

        if self._config is not None and not self._config.use_redis_pubsub:
            return None
        reader = self._get_result_reader()
        return reader.notifier() if reader is not None else None

    # ── Publishing ──────────────────────────────────────────────────────

    def submit(self, submission: TaskSubmission) -> str:
        self._validate_payload(submission)

        task_options: dict[str, Any] = dict(submission.options)
        if submission.queue is not None:
            task_options["queue"] = submission.queue
        if submission.task_id is not None:
            task_options["task_id"] = str(submission.task_id)
        if submission.headers is not None:
            task_options["headers"] = dict(submission.headers)

        try:
            result = self.app.send_task(
                submission.task_name,
                args=list(submission.args),
                kwargs=dict(submission.kwargs),
                **task_options,
            )
        except Exception as exc:
            raise TaskPublishError(
                f"failed to publish task {submission.task_name!r}: {exc}"
            ) from exc

        published_id = getattr(result, "id", None)
        if not published_id:
            raise TaskPublishError(
                f"task {submission.task_name!r} did not return an id"
            )
        logger.debug(
            "published task name=%s id=%s queue=%s",
            submission.task_name,
            published_id,
            submission.queue,
        )
        return str(published_id)

    def _validate_payload(self, submission: TaskSubmission) -> None:
        if not self._payload_validation_enabled:
            return
        payload: dict[str, Any] = {
            "args": list(submission.args),
            "kwargs": dict(submission.kwargs),
        }
        if submission.headers is not None:
            payload["headers"] = dict(submission.headers)
        try:
            json.dumps(payload, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise InvalidTaskPayloadError(
                f"task {submission.task_name!r} payload is not JSON serializable: {exc}"
            ) from exc

    # ── Status ──────────────────────────────────────────────────────────

    def get_status(self, task_id: str) -> TaskStatus:
        try:
            backend = self._resolve_backend()
            # No ``cache`` argument: the RPC backend's signature differs, and
            # Celery only caches terminal SUCCESS metas anyway.
            meta = backend.get_task_meta(task_id)
        except NotImplementedError as exc:
            raise TaskResultUnavailableError(
                "no result backend is configured; set result_backend / "
                "TASK_BRIDGE_RESULT_BACKEND (or CELERY_RESULT_BACKEND) to poll results"
            ) from exc
        except Exception as exc:
            raise TaskResultUnavailableError(
                f"failed to read status for task {task_id!r}: {exc}"
            ) from exc
        return TaskStatus.from_meta(task_id, meta)

    def get_status_many(self, task_ids: Sequence[str]) -> dict[str, TaskStatus]:
        if not task_ids:
            return {}

        backend = self._resolve_backend()
        mget = getattr(backend, "mget", None)
        if mget is None:
            return super().get_status_many(task_ids)

        try:
            raws = mget([backend.get_key_for_task(task_id) for task_id in task_ids])
        except NotImplementedError:
            return super().get_status_many(task_ids)
        except Exception as exc:
            raise TaskResultUnavailableError(f"failed to read task statuses: {exc}") from exc

        statuses: dict[str, TaskStatus] = {}
        for task_id, raw in zip(task_ids, raws, strict=True):
            try:
                meta = (
                    {"status": "PENDING", "result": None}
                    if not raw
                    else backend.decode_result(raw)
                )
            except Exception as exc:
                raise TaskResultUnavailableError(
                    f"failed to decode result for task {task_id!r}: {exc}"
                ) from exc
            statuses[task_id] = TaskStatus.from_meta(task_id, meta)
        return statuses

    async def aget_status(self, task_id: str) -> TaskStatus:
        reader = self._get_result_reader()
        if reader is None:
            return await self._run_blocking(self.get_status, task_id)

        try:
            meta = await reader.get_meta(task_id)
        except Exception as exc:
            raise TaskResultUnavailableError(
                f"failed to read status for task {task_id!r}: {exc}"
            ) from exc
        return TaskStatus.from_meta(task_id, meta)

    async def aget_status_many(
        self, task_ids: Sequence[str]
    ) -> dict[str, TaskStatus]:
        if not task_ids:
            return {}

        reader = self._get_result_reader()
        if reader is None:
            return await self._run_blocking(self.get_status_many, task_ids)

        try:
            metas = await reader.get_many_meta(task_ids)
        except Exception as exc:
            raise TaskResultUnavailableError(f"failed to read task statuses: {exc}") from exc
        return {
            task_id: TaskStatus.from_meta(task_id, metas[task_id])
            for task_id in task_ids
        }

    async def watch(self, task_id: str) -> TaskWatch | None:
        notifier = self._get_result_notifier()
        if notifier is None:
            return None
        event = await notifier.register(task_id)
        if event is None:
            return None
        return _EventWatch(notifier, task_id, event)

    # ── Control / lifecycle ─────────────────────────────────────────────

    def revoke(
        self, task_id: str, *, terminate: bool = False, signal: str | None = None
    ) -> None:
        try:
            self.app.control.revoke(task_id, terminate=terminate, signal=signal)
        except Exception as exc:
            raise BridgeError(f"failed to revoke task {task_id!r}: {exc}") from exc
        logger.debug("revoke requested id=%s terminate=%s", task_id, terminate)

    def warm_up(self, *, connections: int = 1) -> None:
        if connections < 1:
            raise ValueError("connections must be at least 1")
        probe_id = f"task-bridge-warmup:{uuid4()}"
        for _ in range(connections):
            self.get_status(probe_id)

    async def awarm_up(self, *, connections: int = 1) -> None:
        if connections < 1:
            raise ValueError("connections must be at least 1")

        reader = self._get_result_reader()
        try:
            if reader is not None:
                await asyncio.gather(*(reader.ping() for _ in range(connections)))
                return
            probe_id = f"task-bridge-warmup:{uuid4()}"
            await asyncio.gather(
                *(self.aget_status(probe_id) for _ in range(connections))
            )
        except Exception as exc:
            raise TaskResultUnavailableError(
                f"result backend warm-up failed: {exc}"
            ) from exc

    def close(self) -> None:
        """Release the app this transport created; injected apps are untouched."""

        app: Celery | None = None
        if self._owns_app:
            with self._app_lock:
                if self._app is not None:
                    app, self._app = self._app, None
                    self._backend = None
                    if self._reader is not None:
                        logger.debug(
                            "async Redis result client still open; use aclose() to release it"
                        )
                    self._reader = None
                    self._reader_attempted = False
        if app is not None:
            app.close()
        super().close()

    async def aclose(self) -> None:
        reader, self._reader = self._reader, None
        if reader is not None:
            try:
                await reader.aclose()
            except Exception:  # noqa: BLE001 - best-effort shutdown
                logger.debug("failed to close async Redis result client", exc_info=True)
        self._reader_attempted = False
        self.close()


__all__ = ["CeleryTransport"]
