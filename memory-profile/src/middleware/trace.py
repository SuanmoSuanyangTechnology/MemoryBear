"""Trace-id HTTP propagation backed by request-local ContextVar state."""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from src.common.observability.context import (
    TRACE_ID_HEADER,
    normalize_trace_id,
    reset_trace_id,
    set_trace_id,
)


class TraceIdMiddleware(BaseHTTPMiddleware):
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
