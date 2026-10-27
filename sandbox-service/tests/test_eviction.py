"""Pure eviction rules (design doc 3.1): who may be destroyed, and in which order."""

from __future__ import annotations

from src.backends.base import STATE_PAUSED, STATE_RUNNING
from src.services import InstanceRecord, is_candidate, select_victims

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


def test_a_paused_instance_needs_no_minimum_pause_age():
    assert is_candidate(rec("a", paused_at=1000))
    assert is_candidate(rec("b", paused_at=999_999))


def test_running_busy_or_unstamped_are_never_candidates():
    assert not is_candidate(rec("a", state=STATE_RUNNING))
    assert not is_candidate(rec("b", busy=1))
    unstamped = rec("c")
    unstamped.paused_at_ms = None
    assert not is_candidate(unstamped)


def test_longest_paused_goes_first():
    recs = [rec("a", paused_at=300), rec("b", paused_at=100), rec("c", paused_at=200)]
    victims = select_victims(recs, need=2)
    assert [v.digest for v in victims] == ["b", "c"]


def test_need_zero_or_negative_selects_nothing():
    recs = [rec("a")]
    assert select_victims(recs, need=0) == []
    assert select_victims(recs, need=-3) == []


def test_over_quota_tenant_is_evicted_before_older_instances_of_others():
    recs = [
        rec("x1", tenant="X", paused_at=500),
        rec("x2", tenant="X", paused_at=600),
        rec("y1", tenant="Y", paused_at=100),  # oldest, but Y is within its quota
    ]
    victims = select_victims(recs, need=3, soft_quota=1)
    assert [v.digest for v in victims] == ["x1", "x2", "y1"]
    one = select_victims(recs, need=1, soft_quota=1)
    assert [v.digest for v in one] == ["x1"]


def test_only_tenant_filter():
    recs = [rec("a", tenant="A", paused_at=1), rec("b", tenant="B", paused_at=2)]
    victims = select_victims(recs, need=5, only_tenant="B")
    assert [v.digest for v in victims] == ["b"]


def test_protected_instances_are_skipped():
    recs = [
        rec("running", state=STATE_RUNNING),
        rec("busy", busy=2),
        rec("paused", paused_at=5),
    ]
    victims = select_victims(recs, need=10)
    assert [v.digest for v in victims] == ["paused"]


def test_ties_are_broken_by_digest():
    recs = [rec("b", paused_at=5), rec("a", paused_at=5)]
    victims = select_victims(recs, need=2)
    assert [v.digest for v in victims] == ["a", "b"]
