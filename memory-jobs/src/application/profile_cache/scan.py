"""Periodic sweep that dispatches stale insight/summary caches.

The sweep mechanics (groups, paging, lock probe, counters, error isolation) live
in ``src.application.scanning``; this module owns only the profile decisions:
the freshness policy, the skip buckets and the per-user publish.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from src.application.dto import ScanReport
from src.application.ports.lock import InflightLock
from src.application.ports.publishing import Priority, UnitPublisher
from src.application.ports.uow import Repositories, UnitOfWork
from src.application.scanning import (
    SKIP_INFLIGHT_REASON,
    ScanDecision,
    ScanSweep,
)
from src.domain.profile.policies import (
    CacheFreshnessPolicy,
    RefreshDecision,
    RefreshPlan,
)
from src.domain.profile.read_models import RefreshCandidate
from src.domain.shared.ids import EndUserId, WorkspaceId

SKIP_NO_CHANGE = "skip_no_change"
SKIP_FRESH = "skip_fresh"


class ScanProfileCachesUseCase:
    def __init__(
        self,
        *,
        uow: UnitOfWork,
        policy: CacheFreshnessPolicy,
        lock: InflightLock,
        publisher: UnitPublisher,
        language: str,
        page_size: int,
    ) -> None:
        self.policy = policy
        self.publisher = publisher
        self.language = language
        self._sweep: ScanSweep[RefreshCandidate, WorkspaceId] = ScanSweep(
            uow=uow,
            lock=lock,
            groups=_workspace_ids,
            page=_page,
            decide=self._decide,
            dispatch=self._dispatch,
            lock_subject=lambda candidate: str(candidate.end_user_id),
            name="profile cache",
            page_size=page_size,
        )

    def execute(self) -> ScanReport:
        sweep = self._sweep.execute()
        return ScanReport(
            dispatched=sweep.dispatched,
            skip_no_change=sweep.skipped_count(SKIP_NO_CHANGE),
            skip_fresh=sweep.skipped_count(SKIP_FRESH),
            skip_inflight=sweep.skipped_count(SKIP_INFLIGHT_REASON),
            failed=sweep.failed,
            elapsed_seconds=sweep.elapsed_seconds,
        )

    # ------------------------------------------------------- domain decisions

    def _decide(self, candidate: RefreshCandidate, now: datetime) -> ScanDecision:
        plan = self.policy.plan(candidate, now)
        if plan.dispatches_anything:
            return ScanDecision.dispatch_with(plan)
        if (
            plan.insight is RefreshDecision.SKIP_FRESH
            or plan.summary is RefreshDecision.SKIP_FRESH
        ):
            return ScanDecision.skip(SKIP_FRESH)
        return ScanDecision.skip(SKIP_NO_CHANGE)

    def _dispatch(
        self, candidate: RefreshCandidate, plan: RefreshPlan, dispatch_index: int
    ) -> None:
        self.publisher.publish_profile_cache(
            end_user_id=str(candidate.end_user_id),
            workspace_id=str(candidate.workspace_id),
            language=self.language,
            refresh_insight=plan.dispatches_insight,
            refresh_summary=plan.dispatches_summary,
            # The sweep yields to every user-triggered unit.
            priority=Priority.BACKGROUND,
            dispatch_index=dispatch_index,
        )


def _workspace_ids(repositories: Repositories) -> Sequence[WorkspaceId]:
    return repositories.profile_refresh.active_workspace_ids()


def _page(
    repositories: Repositories,
    workspace_id: WorkspaceId,
    after: EndUserId | None,
    limit: int,
) -> tuple[Sequence[RefreshCandidate], EndUserId | None]:
    return repositories.profile_refresh.refresh_candidates(
        workspace_id, after=after, limit=limit
    )
