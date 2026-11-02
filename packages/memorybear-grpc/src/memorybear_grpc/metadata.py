"""Metadata helpers shared by gRPC clients and servers.

Trace ids ride a dedicated metadata key. Deadline budgets propagate through a
ContextVar so deep call sites (model invoke transports) can cap their own
deadline without threading the value through every function signature.
"""

from __future__ import annotations

from collections.abc import Iterable
from contextvars import ContextVar, Token

TRACE_ID_METADATA_KEY = "x-trace-id"

_UPSTREAM_REMAINING: ContextVar[float | None] = ContextVar(
    "grpc_upstream_remaining", default=None
)


def metadata_dict(
    metadata: Iterable[tuple[str, str | bytes]],
) -> dict[str, str]:
    """Flatten invocation metadata; binary keys stay out of the header view."""

    values: dict[str, str] = {}
    for key, value in metadata:
        if key.endswith("-bin"):
            continue
        values[key.lower()] = value.decode() if isinstance(value, bytes) else value
    return values


def trace_metadata(trace_id: str | None) -> tuple[tuple[str, str], ...]:
    if not trace_id:
        return ()
    return ((TRACE_ID_METADATA_KEY, trace_id),)


def bind_upstream_remaining(seconds: float | None) -> Token[float | None]:
    return _UPSTREAM_REMAINING.set(seconds)


def reset_upstream_remaining(token: Token[float | None]) -> None:
    _UPSTREAM_REMAINING.reset(token)


def upstream_remaining() -> float | None:
    return _UPSTREAM_REMAINING.get()


def deadline_for(*, budget_s: float, margin_s: float) -> float:
    """Own budget capped by the caller's remaining time minus a safety margin."""

    remaining = _UPSTREAM_REMAINING.get()
    if remaining is None:
        return budget_s
    return max(0.001, min(budget_s, remaining - margin_s))
