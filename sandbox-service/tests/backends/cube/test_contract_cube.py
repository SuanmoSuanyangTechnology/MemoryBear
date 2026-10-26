"""Run the shared backend contract against Cube, served by the in-process fake."""

from __future__ import annotations

import pytest

from tests.contract.backend_contract import BackendContract


class TestCubeContract(BackendContract):
    @pytest.fixture
    def backend(self, make_backend):
        return make_backend()


class TestCubeContractPathRouting(BackendContract):
    """Same contract with the path-based data-plane addressing."""

    @pytest.fixture
    def backend(self, make_backend):
        return make_backend(route_mode="path")
