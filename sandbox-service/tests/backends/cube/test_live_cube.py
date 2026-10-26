"""Backend contract against a real Cube deployment (deselected by default).

Run with ``pytest -m live tests/backends/cube/test_live_cube.py`` after exporting
``SANDBOX_CUBE_API_URL`` and ``SANDBOX_CUBE_PROXY_ADDR`` (and ``SANDBOX_CUBE_API_KEY``
if the API requires one). Without them the tests are skipped, not failed.

The node may be shared: each test removes only the instances it created itself.
"""

from __future__ import annotations

import os

import pytest

from src.backends.cube import CubeBackend, load_config
from src.clock import Clock
from tests.contract.backend_contract import BackendContract

pytestmark = pytest.mark.live

REQUIRED = ("SANDBOX_CUBE_API_URL", "SANDBOX_CUBE_PROXY_ADDR")


class TestLiveCubeContract(BackendContract):
    @pytest.fixture
    async def backend(self):
        missing = [v for v in REQUIRED if not os.environ.get(v)]
        if missing:
            pytest.skip(f"live Cube not configured: {', '.join(missing)}")
        backend = CubeBackend(load_config(os.environ), Clock())
        if not await backend.health():
            await backend.aclose()
            pytest.skip("live Cube is not reachable")
        before = {i.instance_id for i in await backend.list_owned()}
        try:
            yield backend
        finally:
            for info in await backend.list_owned():
                if info.instance_id not in before:
                    try:
                        await backend.destroy(info.instance_id)
                    except Exception:  # best-effort cleanup of our own instances
                        pass
            await backend.aclose()
