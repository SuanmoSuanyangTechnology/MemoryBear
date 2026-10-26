"""Eviction candidate selection (design doc 3.1) as pure functions.

Only instances that are *paused*, idle, and have been paused for at least
``P_min`` may be destroyed. Running idle instances are never candidates: pausing
frees no scheduler quota by default (K11), so the answer to a full cluster is
"queue, then sandbox_busy", not "pause more".
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from ..backends.base import STATE_PAUSED
from .registry import InstanceRecord


def is_candidate(rec: InstanceRecord, now_ms: int, p_min_ms: int) -> bool:
    return (
        rec.state == STATE_PAUSED
        and rec.busy == 0
        and rec.paused_at_ms is not None
        and now_ms - rec.paused_at_ms >= p_min_ms
    )


def select_victims(
    records: Iterable[InstanceRecord],
    *,
    now_ms: int,
    p_min_ms: int,
    need: int,
    soft_quota: int | None = None,
    only_tenant: str | None = None,
) -> list[InstanceRecord]:
    """Pick up to ``need`` victims.

    Order: tenants above ``soft_quota`` first, then the longest-paused first.
    """

    if need <= 0:
        return []
    recs = list(records)
    per_tenant = Counter(r.tenant_id for r in recs)

    def over_quota(r: InstanceRecord) -> bool:
        return soft_quota is not None and per_tenant[r.tenant_id] > soft_quota

    candidates = [
        r
        for r in recs
        if is_candidate(r, now_ms, p_min_ms) and (only_tenant is None or r.tenant_id == only_tenant)
    ]
    candidates.sort(key=lambda r: (not over_quota(r), r.paused_at_ms, r.digest))
    return candidates[:need]
