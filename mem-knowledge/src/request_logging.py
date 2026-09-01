"""HTTP request lifecycle logging without buffering request or response bodies."""

from __future__ import annotations

import asyncio
import logging
import time

from fastapi import FastAPI
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)


def request_route_template(scope: Scope) -> str:
    route_path = getattr(scope.get("route"), "path", None)
    if not isinstance(route_path, str) or not route_path:
        return "<unresolved>"
    request_path = str(scope.get("path", ""))
    if request_path.startswith("/internal/v1/") and not route_path.startswith("/internal/v1/"):
        return f"/internal/v1{route_path}"
    return route_path


def _trace_id(scope: Scope) -> str:
    state = scope.get("state")
    if isinstance(state, dict):
        value = state.get("trace_id")
        if isinstance(value, str) and value:
            return value
    return "<unresolved>"


def _completion_level(status_code: int, completion: str) -> int:
    if completion in {"cancelled", "error"}:
        return logging.ERROR
    if status_code >= 500:
        return logging.ERROR
    if completion == "closed_early":
        return logging.WARNING
    if status_code >= 400:
        return logging.WARNING
    return logging.INFO


class RequestLoggingMiddleware:
    """Emit one completion record for each HTTP request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started_at = time.perf_counter_ns()
        status_code: int | None = None
        response_bytes = 0
        response_complete = False
        completion = "closed_early"
        headers = Headers(scope=scope)

        async def send_with_metrics(message: Message) -> None:
            nonlocal status_code, response_bytes, response_complete
            await send(message)
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
            elif message["type"] == "http.response.body":
                response_bytes += len(message.get("body", b""))
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                response_complete = True

        try:
            await self.app(scope, receive, send_with_metrics)
            if response_complete:
                completion = "complete"
        except asyncio.CancelledError:
            completion = "cancelled"
            raise
        except Exception:
            completion = "error"
            raise
        finally:
            if status_code is not None:
                final_status = status_code
            elif completion == "error":
                final_status = 500
            else:
                final_status = 0
            duration_ms = max(0, (time.perf_counter_ns() - started_at) // 1_000_000)
            trace_id = _trace_id(scope)
            try:
                logger.log(
                    _completion_level(final_status, completion),
                    "request_completed service=mem-knowledge trace_id=%s method=%s "
                    "route=%s status=%s duration_ms=%s response_bytes=%s completion=%s "
                    "actor_id=%s tenant_id=%s workspace_id=%s source=%s",
                    trace_id,
                    scope.get("method", "<unknown>"),
                    request_route_template(scope),
                    final_status,
                    duration_ms,
                    response_bytes,
                    completion,
                    headers.get("X-KB-Actor-ID"),
                    headers.get("X-KB-Tenant-ID"),
                    headers.get("X-KB-Workspace-ID"),
                    headers.get("X-KB-Source"),
                    extra={"trace_id": trace_id},
                )
            except Exception:
                pass


class RequestLoggingFastAPI(FastAPI):
    """Place request logging outside FastAPI's complete middleware stack."""

    def build_middleware_stack(self) -> ASGIApp:
        return RequestLoggingMiddleware(super().build_middleware_stack())


__all__ = [
    "RequestLoggingFastAPI",
    "RequestLoggingMiddleware",
    "request_route_template",
]
