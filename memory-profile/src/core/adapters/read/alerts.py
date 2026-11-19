"""Retrieval alert adapter.

The host reports degraded retrieval branches to an optional enterprise
notification plugin (``memory_retrieval_failure_reporter``). This service has no
plugin host, so the community default is a no-op that logs the diagnostic at
warning level. An enterprise build can replace this composition seam without
touching the core: the use case only depends on
:class:`src.core.domain.ports.RetrievalAlertPort`.
"""

from __future__ import annotations

import logging

from src.core.domain.errors import MemoryRetrievalBusinessError

logger = logging.getLogger(__name__)


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


def build_alert_port() -> LoggingRetrievalAlertAdapter:
    """Composition entry point for the alert port."""

    return LoggingRetrievalAlertAdapter()


__all__ = ["LoggingRetrievalAlertAdapter", "build_alert_port"]