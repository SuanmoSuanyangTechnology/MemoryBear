"""Pure eviction rules (design doc 3.1): who may be destroyed, and in which order."""

from __future__ import annotations

from src.backends.base import STATE_PAUSED, STATE_RUNNING
from src.services import InstanceRecord, is_candidate, select_victims

NOW = 10_000_000
P_MIN = 1000


def rec(digest, *, tenant="t", state=STATE_PAUSED, paused_at=0, busy=0):
    return InstanceRecord(
        digest,
        tenant,
        state,
        created_ms=0,
        last_active_ms=0,
        instance_id=digest,
        paused_at_ms=paused_at if state == STATE_PAUSED else None,
        busy=busy,
    )


def test_candidate_needs_p_min_of_pause():
    r = rec("a", paused_at=1000)
    assert not is_candidate(r, now_ms=1999, p_min_ms=P_MIN)
    assert is_candidate(r, now_ms=2000, p_min_ms=P_MIN)


def test_running_busy_or_unstamped_are_never_candidates():
    assert not is_candidate(rec("a", state=STATE_RUNNING), NOW, P_MIN)
    assert not is_candidate(rec("b", busy=1), NOW, P_MIN)
    unstamped = rec("c")
    unstamped.paused_at_ms = None
    assert not is_candidate(unstamped, NOW, P_MIN)


def test_longest_paused_goes_first():
    recs = [rec("a", paused_at=300), rec("b", paused_at=100), rec("c", paused_at=200)]
    victims = select_victims(recs, now_ms=NOW, p_min_ms=P_MIN, need=2)
    assert [v.digest for v in victims] == ["b", "c"]


def test_need_zero_or_negative_selects_nothing():
    recs = [rec("a")]
    assert select_victims(recs, now_ms=NOW, p_min_ms=P_MIN, need=0) == []
    assert select_victims(recs, now_ms=NOW, p_min_ms=P_MIN, need=-3) == []


def test_over_quota_tenant_is_evicted_before_older_instances_of_others():
    recs = [
        rec("x1", tenant="X", paused_at=500),
        rec("x2", tenant="X", paused_at=600),
        rec("y1", tenant="Y", paused_at=100),  # oldest, but Y is within its quota
    ]
    victims = select_victims(recs, now_ms=NOW, p_min_ms=P_MIN, need=3, soft_quota=1)
    assert [v.digest for v in victims] == ["x1", "x2", "y1"]
    one = select_victims(recs, now_ms=NOW, p_min_ms=P_MIN, need=1, soft_quota=1)
    assert [v.digest for v in one] == ["x1"]


def test_only_tenant_filter():
    recs = [rec("a", tenant="A", paused_at=1), rec("b", tenant="B", paused_at=2)]
    victims = select_victims(recs, now_ms=NOW, p_min_ms=P_MIN, need=5, only_tenant="B")
    assert [v.digest for v in victims] == ["b"]


def test_protected_instances_are_skipped():
    recs = [
        rec("running", state=STATE_RUNNING),
        rec("busy", busy=2),
        rec("fresh", paused_at=NOW - 10),
        rec("old", paused_at=0),
    ]
    victims = select_victims(recs, now_ms=NOW, p_min_ms=P_MIN, need=10)
    assert [v.digest for v in victims] == ["old"]


def test_ties_are_broken_by_digest():
    recs = [rec("b", paused_at=5), rec("a", paused_at=5)]
    victims = select_victims(recs, now_ms=NOW, p_min_ms=P_MIN, need=2)
    assert [v.digest for v in victims] == ["a", "b"]
