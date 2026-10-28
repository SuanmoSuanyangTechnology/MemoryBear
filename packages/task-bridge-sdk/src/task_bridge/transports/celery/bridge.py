"""Celery assembly of the generic :class:`~task_bridge.core.bridge.TaskBridge`."""

from __future__ import annotations

from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor

from celery import Celery

from task_bridge.core.bridge import TaskBridge
from task_bridge.transports.celery.config import CeleryConfig
from task_bridge.transports.celery.transport import CeleryTransport


class CeleryBridge(TaskBridge):
    """``TaskBridge`` wired to :class:`CeleryTransport`.

    Keeps the pre-abstraction public surface: ``config``, ``app`` and
    ``from_env``.
    """

    def __init__(
        self,
        config: CeleryConfig | None = None,
        *,
        app: Celery | None = None,
        validate_payload: bool | None = None,
        executor: ThreadPoolExecutor | None = None,
        executor_workers: int | None = None,
        poll_jitter: float | None = None,
    ) -> None:
        transport = CeleryTransport(
            config=config,
            app=app,
            validate_payload=validate_payload,
            executor=executor,
            executor_workers=executor_workers,
        )
        if poll_jitter is None:
            poll_jitter = transport.config.poll_jitter if transport.config else 0.1
        super().__init__(transport, poll_jitter=poll_jitter)

    @property
    def _celery(self) -> CeleryTransport:
        transport = self.transport
        assert isinstance(transport, CeleryTransport)
        return transport

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> CeleryBridge:
        return cls(CeleryConfig.from_env(environ))

    @property
    def config(self) -> CeleryConfig | None:
        return self._celery.config

    @property
    def app(self) -> Celery:
        return self._celery.app


__all__ = ["CeleryBridge"]
