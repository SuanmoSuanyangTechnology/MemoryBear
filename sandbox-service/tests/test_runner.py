from __future__ import annotations

import asyncio

import pytest

from src.backends import FakeBackend
from src.services import SandboxManager, SchedulerConfig
from src.services.runner import BackgroundRunner

FAST = SchedulerConfig(tick_interval_s=0.01, reconcile_interval_s=0.01)


class _Manager:
    """Counts calls; ``tick`` fails on its first call."""

    def __init__(self) -> None:
        self.ticks = 0
        self.reconciles = 0

    async def tick(self):
        self.ticks += 1
        if self.ticks == 1:
            raise RuntimeError("boom")

    async def reconcile(self):
        self.reconciles += 1


async def _until(predicate, timeout=2.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.005)


async def test_both_loops_run_repeatedly_and_survive_a_failure():
    mgr = _Manager()
    runner = BackgroundRunner(mgr, FAST)
    runner.start()
    try:
        await _until(lambda: mgr.ticks >= 3 and mgr.reconciles >= 3)
        assert runner.failures["tick"] == 1
        assert runner.failures["reconcile"] == 0
        assert runner.running
    finally:
        await runner.stop()
    assert not runner.running


async def test_stop_halts_the_loops():
    mgr = _Manager()
    runner = BackgroundRunner(mgr, FAST)
    runner.start()
    await _until(lambda: mgr.ticks >= 2)
    await runner.stop()
    seen = (mgr.ticks, mgr.reconciles)
    await asyncio.sleep(0.05)
    assert (mgr.ticks, mgr.reconciles) == seen


async def test_start_twice_is_rejected_and_stop_is_idempotent():
    runner = BackgroundRunner(_Manager(), FAST)
    runner.start()
    with pytest.raises(RuntimeError):
        runner.start()
    await runner.stop()
    await runner.stop()


async def test_runner_drives_a_real_manager():
    backend = FakeBackend()
    mgr = SandboxManager(backend, FAST)
    runner = BackgroundRunner(mgr, FAST)
    runner.start()
    try:
        await _until(lambda: runner.rounds["tick"] >= 2 and runner.rounds["reconcile"] >= 2)
        assert runner.failures == {"tick": 0, "reconcile": 0}
    finally:
        await runner.stop()


def test_intervals_must_be_positive():
    with pytest.raises(ValueError):
        SchedulerConfig(tick_interval_s=0)
    with pytest.raises(ValueError):
        SchedulerConfig(reconcile_interval_s=-1)
