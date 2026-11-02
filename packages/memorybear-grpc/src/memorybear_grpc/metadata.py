"""Metadata helpers shared by gRPC clients and servers.

Trace ids ride a dedicated metadata key; the W3C ``traceparent`` header is
accepted as the standard carrier and its trace-id is used when ``x-trace-id``
is absent. Deadline budgets propagate through a ContextVar so deep call sites
(model invoke transports) can cap their own deadline without threading the
value through every function signature.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterable
from contextvars import ContextVar, Token

TRACE_ID_METADATA_KEY = "x-trace-id"
TRACEPARENT_METADATA_KEY = "traceparent"

_TRACEPARENT_RE = re.compile(
    r"^([0-9a-f]{2})-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$"
)
_ZERO_TRACE_ID = "0" * 32
_ZERO_SPAN_ID = "0" * 16

# Absolute ``time.monotonic()`` deadline captured when the inbound RPC starts;
# a stored remaining-seconds snapshot would not decay while the handler works.
_UPSTREAM_DEADLINE: ContextVar[float | None] = ContextVar(
    "grpc_upstream_deadline", default=None
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


def traceparent_trace_id(value: str | None) -> str | None:
    """Return the W3C trace-id when ``value`` is a valid ``traceparent``.

    Version ``ff`` and all-zero ids are invalid. Version ``00`` must be
    exactly 55 characters; later versions may append fields but the first
    55 characters keep the same layout.
    """

    if not value:
        return None
    candidate = value.strip()
    if len(candidate) < 55:
        return None
    match = _TRACEPARENT_RE.fullmatch(candidate[:55])
    if match is None:
        return None
    version, trace_id, span_id, _flags = match.groups()
    if version == "ff":
        return None
    if version == "00" and len(candidate) != 55:
        return None
    if len(candidate) > 55 and candidate[55] != "-":
        return None
    if trace_id == _ZERO_TRACE_ID or span_id == _ZERO_SPAN_ID:
        return None
    return trace_id


def trace_id_from_metadata(
    metadata: Iterable[tuple[str, str | bytes]],
) -> str | None:
    """Prefer the explicit ``x-trace-id``; fall back to the W3C trace-id."""

    headers = metadata_dict(metadata)
    trace_id = headers.get(TRACE_ID_METADATA_KEY)
    if trace_id:
        return trace_id
    return traceparent_trace_id(headers.get(TRACEPARENT_METADATA_KEY))


def bind_upstream_remaining(seconds: float | None) -> Token[float | None]:
    """Capture the caller's remaining budget as an absolute monotonic deadline."""

    deadline = None if seconds is None else time.monotonic() + max(0.0, seconds)
    return _UPSTREAM_DEADLINE.set(deadline)


def reset_upstream_remaining(token: Token[float | None]) -> None:
    _UPSTREAM_DEADLINE.reset(token)


def upstream_remaining() -> float | None:
    """Live remaining time; it keeps shrinking while the handler runs."""

    deadline = _UPSTREAM_DEADLINE.get()
    if deadline is None:
        return None
    return max(0.0, deadline - time.monotonic())


def deadline_for(*, budget_s: float, margin_s: float) -> float:
    """Own budget capped by the caller's live remaining time minus a safety margin."""

    remaining = upstream_remaining()
    if remaining is None:
        return budget_s
    return max(0.001, min(budget_s, remaining - margin_s))
