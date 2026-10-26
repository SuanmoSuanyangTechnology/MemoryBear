from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.backends import FakeBackend
from src.clock import ManualClock
from src.services import SandboxManager, SchedulerConfig


@dataclass
class Env:
    clock: ManualClock
    backend: FakeBackend
    mgr: SandboxManager


@pytest.fixture
def make_env():
    """Factory: ``make_env(backend_cls=FakeBackend, **scheduler_config_overrides)``."""

    def _make(backend_cls=FakeBackend, **cfg) -> Env:
        clock = ManualClock()
        backend = backend_cls(clock)
        mgr = SandboxManager(backend, SchedulerConfig(**cfg), clock=clock)
        return Env(clock, backend, mgr)

    return _make
