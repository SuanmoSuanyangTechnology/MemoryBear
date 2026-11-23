"""Retrieval alert adapter.

The host reports degraded retrieval branches to an optional enterprise
notification center. Two implementations share the
:class:`src.core.domain.ports.RetrievalAlertPort` contract:

- :class:`LoggingRetrievalAlertAdapter` — the open build: logs the diagnostic at
  warning level and does nothing else;
- :class:`MemoryEnterpriseRetrievalAlertAdapter` — posts the diagnostic to the
  closed-source ``memory-enterprise`` service, which persists the notification
  obligation and dispatches the API worker's emit task.

The composition root picks one from the runtime (``MEMORY_ENTERPRISE_BASE_URL``
present → enterprise), so the read pipeline keeps depending only on the port.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

from src.core.domain.errors import MemoryRetrievalBusinessError

logger = logging.getLogger(__name__)

#: Same bound the API host applies to its own alert enqueue: an alert must not
#: extend the retrieval request beyond this, and the enterprise service keeps
#: the obligation even if this caller gives up waiting.
ALERT_DELIVERY_TIMEOUT_SECONDS = 1.0


class _EnterpriseAlertReporter(Protocol):
    """The slice of ``MemoryEnterpriseManager`` this adapter needs."""

    @property
    def enabled(self) -> bool: ...

    async def report_retrieval_alert(self, **kwargs: Any) -> dict[str, Any]: ...


class LoggingRetrievalAlertAdapter:
    """Log-only implementation of the retrieval alert port."""

    async def notify(
        self,
        error: MemoryRetrievalBusinessError,
        *,
        operation_id: str,
        tenant_id: str,
        workspace_id: str,
        end_user_id: str,
    ) -> None:
        logger.warning(
            "retrieval_branch_degraded operation_id=%s code=%s stage=%s impact=%s "
            "model_type=%s tenant=%s workspace=%s end_user=%s",
            operation_id,
            error.code,
            error.stage,
            error.impact,
            error.model_type,
            tenant_id,
            workspace_id,
            end_user_id,
        )


class MemoryEnterpriseRetrievalAlertAdapter:
    """Post degraded branches to the enterprise notification center.

    Never raises: a dead notification path must not change the retrieval
    result, and the retrieval error itself is re-raised by the pipeline.
    """

    def __init__(
        self,
        reporter: _EnterpriseAlertReporter,
        *,
        timeout_seconds: float = ALERT_DELIVERY_TIMEOUT_SECONDS,
    ) -> None:
        self._reporter = reporter
        self._timeout_seconds = timeout_seconds

    async def notify(
        self,
        error: MemoryRetrievalBusinessError,
        *,
        operation_id: str,
        tenant_id: str,
        workspace_id: str,
        end_user_id: str,
    ) -> None:
        try:
            await asyncio.wait_for(
                self._reporter.report_retrieval_alert(
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    end_user_id=end_user_id,
                    operation_id=operation_id,
                    error_code=error.code,
                    stage=error.stage,
                    impact=error.impact,
                    model_type=error.model_type,
                ),
                timeout=self._timeout_seconds,
            )
        except Exception:
            logger.exception(
                "retrieval alert delivery failed operation_id=%s code=%s",
                operation_id,
                error.code,
            )


def build_alert_port(
    memory_enterprise: _EnterpriseAlertReporter | None = None,
) -> LoggingRetrievalAlertAdapter | MemoryEnterpriseRetrievalAlertAdapter:
    """Composition entry point for the alert port.

    An enterprise deployment points the service at ``memory-enterprise``; the
    open build has no such URL and keeps the log-only adapter.
    """

    if memory_enterprise is not None and memory_enterprise.enabled:
        return MemoryEnterpriseRetrievalAlertAdapter(memory_enterprise)
    return LoggingRetrievalAlertAdapter()


__all__ = [
    "ALERT_DELIVERY_TIMEOUT_SECONDS",
    "LoggingRetrievalAlertAdapter",
    "MemoryEnterpriseRetrievalAlertAdapter",
    "build_alert_port",
]