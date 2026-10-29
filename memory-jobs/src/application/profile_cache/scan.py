"""Periodic sweep that dispatches stale insight/summary caches.

The use case enumerates and dispatches; it never reads metadata or calls the
model. One read scope per workspace keeps the connection short, and publishing
happens after the scope closes. A refused publish isolates that user, exactly as
the API's scanner did.
"""

from __future__ import annotations

import logging
import time

from src.application.dto import ScanReport
from src.application.ports.lock import InflightLock
from src.application.ports.publishing import Priority, UnitPublisher
from src.application.ports.uow import UnitOfWork
from src.domain.profile.policies import CacheFreshnessPolicy, RefreshDecision
from src.domain.shared.clock import utc_now_naive

logger = logging.getLogger(__name__)


class ScanProfileCachesUseCase:
    def __init__(
        self,
        *,
        uow: UnitOfWork,
        policy: CacheFreshnessPolicy,
        lock: InflightLock,
        publisher: UnitPublisher,
        language: str,
    ) -> None:
        self.uow = uow
        self.policy = policy
        self.lock = lock
        self.publisher = publisher
        self.language = language

    def execute(self) -> ScanReport:
        started_at = time.monotonic()
        # One "now" for the whole sweep: every user is judged against the same
        # instant, so a long scan does not gradually widen the freshness window.
        now = utc_now_naive()

        dispatched = 0
        skip_no_change = 0
        skip_fresh = 0
        skip_inflight = 0
        failed = 0

        with self.uow.read() as repositories:
            workspace_ids = repositories.profile_refresh.active_workspace_ids()

        for workspace_id in workspace_ids:
            with self.uow.read() as repositories:
                candidates = repositories.profile_refresh.refresh_candidates(
                    workspace_id
                )
            for candidate in candidates:
                try:
                    plan = self.policy.plan(candidate, now)
                    if not plan.dispatches_anything:
                        if (
                            RefreshDecision.SKIP_FRESH is plan.insight
                            or RefreshDecision.SKIP_FRESH is plan.summary
                        ):
                            skip_fresh += 1
                        else:
                            skip_no_change += 1
                        continue

                    end_user_id = str(candidate.end_user_id)
                    if self.lock.is_locked(end_user_id):
                        skip_inflight += 1
                        continue

                    self.publisher.publish_profile_cache(
                        end_user_id=end_user_id,
                        workspace_id=str(candidate.workspace_id),
                        language=self.language,
                        refresh_insight=plan.dispatches_insight,
                        refresh_summary=plan.dispatches_summary,
                        priority=Priority.BACKGROUND,
                        index=dispatched,
                    )
                except Exception:
                    failed += 1
                    logger.error(
                        "profile cache scan dispatch failed user=%s workspace=%s",
                        str(candidate.end_user_id),
                        workspace_id,
                        exc_info=True,
                        extra={"event": "profile_cache_scan_user_failed"},
                    )
                    continue
                dispatched += 1
                if dispatched % 50 == 0:
                    logger.info(
                        "profile cache scan progress dispatched=%s",
                        dispatched,
                        extra={"event": "profile_cache_scan_progress"},
                    )

        elapsed_seconds = time.monotonic() - started_at
        logger.info(
            "profile cache scan completed dispatched=%s skip_no_change=%s skip_fresh=%s "
            "skip_inflight=%s failed=%s elapsed=%.3fs",
            dispatched,
            skip_no_change,
            skip_fresh,
            skip_inflight,
            failed,
            elapsed_seconds,
            extra={"event": "profile_cache_scan_completed"},
        )
        return ScanReport(
            dispatched=dispatched,
            skip_no_change=skip_no_change,
            skip_fresh=skip_fresh,
            skip_inflight=skip_inflight,
            failed=failed,
            elapsed_seconds=elapsed_seconds,
        )
