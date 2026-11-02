"""Trace context and HTTP propagation."""

from __future__ import annotations

import re
import uuid
from contextvars import ContextVar, Token

from memorybear_grpc.metadata import traceparent_trace_id
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

TRACE_ID_HEADER = "X-Trace-Id"
TRACEPARENT_HEADER = "traceparent"
_TRACE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_trace_id: ContextVar[str] = ContextVar("app_trace_id", default="")
_traceparent: ContextVar[str] = ContextVar("app_traceparent", default="")


def get_trace_id() -> str:
    return _trace_id.get()


def set_trace_id(value: str) -> Token[str]:
    return _trace_id.set(value)


def reset_trace_id(token: Token[str]) -> None:
    _trace_id.reset(token)


def get_traceparent() -> str:
    return _traceparent.get()


def set_traceparent(value: str) -> Token[str]:
    return _traceparent.set(value)


def reset_traceparent(token: Token[str]) -> None:
    _traceparent.reset(token)


def normalize_trace_id(value: str | None) -> str:
    if value and _TRACE_ID_PATTERN.fullmatch(value):
        return value
    return uuid.uuid4().hex


def resolve_trace(headers) -> tuple[str, str | None]:
    """Resolve the trace id and the validated traceparent carried by a request.

    ``x-trace-id`` wins for log correlation; a valid W3C ``traceparent`` is the
    fallback source and is kept so it can be forwarded on the next hop.
    """

    traceparent = headers.get(TRACEPARENT_HEADER)
    if traceparent_trace_id(traceparent) is None:
        traceparent = None
    trace_id = headers.get(TRACE_ID_HEADER) or traceparent_trace_id(traceparent)
    return normalize_trace_id(trace_id), traceparent


class TraceIdMiddleware(BaseHTTPMiddleware):
    """Validate or generate a trace ID for every internal request."""

    async def dispatch(self, request: Request, call_next) -> Response:
        trace_id, traceparent = resolve_trace(request.headers)
        token = set_trace_id(trace_id)
        traceparent_token = set_traceparent(traceparent) if traceparent else None
        request.state.trace_id = trace_id
        request.state.traceparent = traceparent
        try:
            response = await call_next(request)
        finally:
            if traceparent_token is not None:
                reset_traceparent(traceparent_token)
            reset_trace_id(token)
        response.headers[TRACE_ID_HEADER] = trace_id
        if traceparent:
            response.headers[TRACEPARENT_HEADER] = traceparent
        return response
