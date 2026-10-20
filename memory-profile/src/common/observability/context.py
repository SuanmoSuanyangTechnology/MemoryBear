"""Request-scoped observability context."""

from __future__ import annotations

import re
import uuid
from contextvars import ContextVar, Token

TRACE_ID_HEADER = "X-Trace-Id"
_TRACE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_trace_id: ContextVar[str] = ContextVar("memory_trace_id", default="")


def get_trace_id() -> str:
    return _trace_id.get()


def set_trace_id(value: str) -> Token[str]:
    return _trace_id.set(value)


def reset_trace_id(token: Token[str]) -> None:
    _trace_id.reset(token)


def normalize_trace_id(value: str | None) -> str:
    if value and _TRACE_ID_PATTERN.fullmatch(value):
        return value
    return uuid.uuid4().hex
