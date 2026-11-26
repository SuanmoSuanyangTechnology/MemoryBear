"""Enterprise plugin bridge for alert metrics (login anomalies / quota usage).

Callers hand over raw business facts; the private package owns hashing and
Celery delivery. Alerting is best-effort: every failure is swallowed so a
business transaction result never depends on notification infrastructure.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from src import db
from src.plugins import get_plugin

logger = logging.getLogger(__name__)

LOGIN_ANOMALY_PLUGIN = "login_anomaly_alert_reporter"
QUOTA_USAGE_PLUGIN = "quota_usage_alert_reporter"


def report_login_failure(
    *,
    principal: str,
    auth_surface: str,
    reason_class: str,
    tenant_id: UUID | str | None = None,
    principal_kind: str = "account",
) -> bool:
    """Forward one failed login to the enterprise reporter (no-op when absent)."""

    reporter = get_plugin(LOGIN_ANOMALY_PLUGIN)
    if reporter is None:
        return False
    try:
        return bool(
            reporter.report_failure(
                principal=principal,
                auth_surface=auth_surface,
                reason_class=reason_class,
                tenant_id=tenant_id,
                principal_kind=principal_kind,
            )
        )
    except Exception:
        logger.error(
            "[login-anomaly-alert] reporter failed surface=%s",
            auth_surface,
            exc_info=True,
        )
        return False


def report_quota_change_sync(
    *,
    tenant_id: UUID,
    quota_type: str,
    workspace_id: UUID | None = None,
) -> bool:
    """Re-aggregate usage after commit and hand the snapshot to the reporter.

    Must be called only after the business transaction has committed: the
    reporter evaluates a fresh snapshot instead of re-opening that unit of
    work (same contract as the monolith's ``QuotaUsageAlertReporter``).
    """

    reporter = get_plugin(QUOTA_USAGE_PLUGIN)
    if reporter is None:
        return False
    try:
        from src.services.quota import get_quota_snapshot

        with db.get_db_context() as session:
            usage: dict[str, Any] | None = get_quota_snapshot(
                session, tenant_id, quota_type, workspace_id
            )
        if not usage or usage.get("percentage") is None:
            return False
        return bool(
            reporter.report(
                tenant_id=tenant_id,
                quota_type=quota_type,
                workspace_id=workspace_id,
                usage=usage,
            )
        )
    except Exception:
        logger.error(
            "[quota-alert] reporter failed tenant_id=%s quota_type=%s",
            tenant_id,
            quota_type,
            exc_info=True,
        )
        return False
