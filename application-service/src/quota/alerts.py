"""Post-success quota-usage alert seam.

Port of the monolith's ``quota_manager`` alert entry points. In the monolith
the admission-check decorators wrap the handler, so the alert evaluation runs
automatically after a successful business call; here the management-plane
handlers call ``report_quota_change_sync`` explicitly after their commit. The
reporter itself comes from the plugin registry (enterprise policy layer); with
no reporter registered this is a no-op, and reporter failures never affect the
business response.
"""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from ..plugins import get_plugin

logger = logging.getLogger(__name__)


async def report_quota_change(
    tenant_id: UUID,
    quota_type: str,
    workspace_id: UUID | None = None,
) -> None:
    """Async alert entry: call after the usage-changing transaction commits."""

    try:
        reporter = get_plugin("quota_usage_alert_reporter")
        if reporter is not None:
            await reporter.evaluate(
                tenant_id=tenant_id,
                quota_type=quota_type,
                workspace_id=workspace_id,
            )
    except Exception as error:
        logger.error(
            "Quota alert evaluation failed: tenant=%s, workspace=%s, type=%s, error=%s",
            tenant_id,
            workspace_id,
            quota_type,
            error,
            exc_info=True,
        )


def report_quota_change_sync(
    tenant_id: UUID,
    quota_type: str,
    workspace_id: UUID | None = None,
) -> None:
    """Sync alert entry for management-plane handlers and background tasks."""

    try:
        reporter = get_plugin("quota_usage_alert_reporter")
        if reporter is None:
            return
        evaluation = reporter.evaluate(
            tenant_id=tenant_id,
            quota_type=quota_type,
            workspace_id=workspace_id,
        )
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(evaluation)
        else:
            # A caller running inside an event loop must not nest asyncio.run.
            loop.create_task(evaluation)
    except Exception as error:
        logger.error(
            "Quota alert evaluation failed: tenant=%s, workspace=%s, type=%s, error=%s",
            tenant_id,
            workspace_id,
            quota_type,
            error,
            exc_info=True,
        )


__all__ = ["report_quota_change", "report_quota_change_sync"]
