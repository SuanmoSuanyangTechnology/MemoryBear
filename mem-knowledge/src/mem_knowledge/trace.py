"""Trace context and HTTP propagation."""

from __future__ import annotations

import re
import uuid
from contextvars import ContextVar, Token

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

TRACE_ID_HEADER = "X-Trace-Id"
_TRACE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_trace_id: ContextVar[str] = ContextVar("mem_knowledge_trace_id", default="")


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


class TraceIdMiddleware(BaseHTTPMiddleware):
    """Validate or generate a trace ID for every internal request."""

    async def dispatch(self, request: Request, call_next) -> Response:
        trace_id = normalize_trace_id(request.headers.get(TRACE_ID_HEADER))
        token = set_trace_id(trace_id)
        request.state.trace_id = trace_id
        try:
            response = await call_next(request)
        finally:
            reset_trace_id(token)
        response.headers[TRACE_ID_HEADER] = trace_id
        return response
