"""Run the shared backend contract against the in-memory fake."""

from __future__ import annotations

import pytest

from src.backends import FakeBackend
from src.clock import ManualClock
from tests.contract.backend_contract import BackendContract


class TestFakeContract(BackendContract):
    @pytest.fixture
    def backend(self):
        return FakeBackend(ManualClock())
