"""System clock adapter."""

from __future__ import annotations

from datetime import datetime

from src.domain.shared.clock import utc_now_naive


class SystemClock:
    def now(self) -> datetime:
        return utc_now_naive()
