from __future__ import annotations

import pytest

from src.backends.cube.backend import CubeBackend
from src.backends.cube.client import CubeClient
from src.backends.cube.config import CubeConfig
from src.clock import ManualClock
from tests.backends.cube.fake_cube import API_URL, PROXY_ADDR, FakeCube


def make_config(**over) -> CubeConfig:
    base = {"api_url": API_URL, "proxy_addr": PROXY_ADDR}
    base.update(over)
    return CubeConfig(**base)


@pytest.fixture
def clock():
    return ManualClock()


@pytest.fixture
def cube(clock):
    return FakeCube(clock)


@pytest.fixture
def make_backend(clock, cube):
    """``make_backend(**config_overrides)`` -> backend wired to the in-process fake."""

    def _make(**over) -> CubeBackend:
        cfg = make_config(**over)
        # MockTransport holds no sockets, so the client needs no explicit close here.
        return CubeBackend(cfg, clock, CubeClient(cfg, transport=cube.transport()))

    return _make
