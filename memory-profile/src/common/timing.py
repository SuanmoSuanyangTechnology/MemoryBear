"""Monotonic-clock helpers shared by budgeted calls."""

from __future__ import annotations

import time


def remaining_seconds(started_at: float, budget_seconds: float) -> float:
    """Seconds left in a budget measured from ``time.monotonic()``.

    The result is clamped at zero, so an exhausted budget is always ``<= 0``
    without callers re-implementing the subtraction.
    """

    return max(0.0, budget_seconds - (time.monotonic() - started_at))
