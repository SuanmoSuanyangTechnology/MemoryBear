"""Shared sweep template: enumerate, decide, probe, dispatch, count.

Every periodic scanner in this service has the same shape: a workspace-scoped
candidate list, a per-candidate decision, a per-user in-flight probe and one
publish per dispatched unit. A domain supplies the candidate source, its
decision and its publish action; the mechanics, the skip counters, error
isolation and progress logging live here so a new domain does not copy them.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from src.application.ports.lock import InflightLock
from src.application.ports.uow import Repositories, UnitOfWork
from src.domain.shared.clock import utc_now_naive

logger = logging.getLogger(__name__)

#: Skip bucket every sweep shares: the user's unit is already running.
SKIP_INFLIGHT_REASON = "skip_inflight"

#: Log a progress line every this many dispatched units.
PROGRESS_EVERY = 50


@dataclass(frozen=True, slots=True)
class ScanDecision:
    """One candidate's verdict, plus whatever the domain's dispatch needs."""

    dispatch: bool
    #: Counter bucket for a skipped candidate, e.g. ``skip_fresh``.
    reason: str | None = None
    #: Opaque to the sweep; handed back to the domain's dispatch action.
    payload: Any = None

    @classmethod
    def dispatch_with(cls, payload: Any = None) -> ScanDecision:
        return cls(dispatch=True, payload=payload)

    @classmethod
    def skip(cls, reason: str) -> ScanDecision:
        return cls(dispatch=False, reason=reason)


@dataclass(frozen=True, slots=True)
class SweepReport:
    """What one sweep did, keyed by the domain's own skip reasons."""

    dispatched: int = 0
    skipped: dict[str, int] = field(default_factory=dict)
    failed: int = 0
    elapsed_seconds: float = 0.0

    def skipped_count(self, reason: str) -> int:
        return self.skipped.get(reason, 0)


class ScanSweep[C, G]:
    """Run one paged sweep over a domain's candidates.

    ``groups`` is the outer dimension — workspaces for a per-user sweep, a
    single ``None`` sentinel for a global one. ``page`` returns at most
    ``page_size`` candidates plus the cursor for the next page; a ``None``
    cursor means the group is exhausted, and ``None`` as the input cursor asks
    for the first page. Every page gets its own short read scope, so memory is
    bounded by the page and no connection is held while units are published.

    ``lock_subject`` is the per-unit lock key, and ``dispatch`` gets the
    candidate, the decision payload and the global dispatched index (used for
    stagger delays).
    """

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        lock: InflightLock,
        groups: Callable[[Repositories], Sequence[G]],
        page: Callable[
            [Repositories, G, Any | None, int],
            tuple[Sequence[C], Any | None],
        ],
        decide: Callable[[C, datetime], ScanDecision],
        dispatch: Callable[[C, Any, int], None],
        lock_subject: Callable[[C], str],
        name: str,
        page_size: int,
        now: Callable[[], datetime] = utc_now_naive,
    ) -> None:
        self._uow = uow
        self._lock = lock
        self._groups = groups
        self._page = page
        self._decide = decide
        self._dispatch = dispatch
        self._lock_subject = lock_subject
        self._name = name
        self._page_size = page_size
        self._event_prefix = name.replace(" ", "_")
        self._now = now

    def execute(self) -> SweepReport:
        started_at = time.monotonic()
        # One "now" for the whole sweep: every candidate is judged against the
        # same instant, so a long scan does not gradually widen its window.
        now = self._now()

        dispatched = 0
        skipped: dict[str, int] = {}
        failed = 0

        with self._uow.read() as repositories:
            groups = self._groups(repositories)

        for group in groups:
            cursor: Any | None = None
            while True:
                with self._uow.read() as repositories:
                    batch, next_cursor = self._page(
                        repositories, group, cursor, self._page_size
                    )
                if not batch:
                    if next_cursor is not None:
                        # The page contract says an empty page ends the group;
                        # a cursor here would silently skip the rest.
                        logger.warning(
                            "%s scan page returned no rows but a cursor; "
                            "stopping group=%s cursor=%s",
                            self._name,
                            group,
                            next_cursor,
                            extra={
                                "event": (
                                    f"{self._event_prefix}_scan_empty_page_with_cursor"
                                ),
                            },
                        )
                    break
                for candidate in batch:
                    subject = ""
                    try:
                        subject = self._lock_subject(candidate)
                        decision = self._decide(candidate, now)
                        if not decision.dispatch:
                            reason = decision.reason or "skip"
                            skipped[reason] = skipped.get(reason, 0) + 1
                            continue
                        if self._lock.is_locked(subject):
                            skipped[SKIP_INFLIGHT_REASON] = (
                                skipped.get(SKIP_INFLIGHT_REASON, 0) + 1
                            )
                            continue
                        self._dispatch(candidate, decision.payload, dispatched)
                    except Exception:
                        failed += 1
                        logger.error(
                            "%s scan unit failed group=%s subject=%s",
                            self._name,
                            group,
                            subject,
                            exc_info=True,
                            extra={
                                "event": f"{self._event_prefix}_scan_user_failed",
                            },
                        )
                        continue
                    dispatched += 1
                    if dispatched % PROGRESS_EVERY == 0:
                        logger.info(
                            "%s scan progress dispatched=%s",
                            self._name,
                            dispatched,
                            extra={
                                "event": f"{self._event_prefix}_scan_progress",
                            },
                        )
                if next_cursor is None:
                    break
                if next_cursor == cursor:
                    # A full page that hands back the same cursor would loop
                    # forever; stop instead of silently re-reading it.
                    logger.warning(
                        "%s scan cursor did not advance group=%s cursor=%s; stopping",
                        self._name,
                        group,
                        cursor,
                        extra={
                            "event": f"{self._event_prefix}_scan_cursor_stuck",
                        },
                    )
                    break
                cursor = next_cursor

        elapsed_seconds = time.monotonic() - started_at
        logger.info(
            "%s scan completed dispatched=%s skipped=%s failed=%s elapsed=%.3fs",
            self._name,
            dispatched,
            skipped,
            failed,
            elapsed_seconds,
            extra={"event": f"{self._event_prefix}_scan_completed"},
        )
        return SweepReport(
            dispatched=dispatched,
            skipped=skipped,
            failed=failed,
            elapsed_seconds=elapsed_seconds,
        )
