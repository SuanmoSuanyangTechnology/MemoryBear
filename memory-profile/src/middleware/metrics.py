"""Per-process HTTP metrics collection for the runtime monitor."""

from __future__ import annotations

import time

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.common.observability.http_metrics import HttpMetrics


def _route_template(scope: Scope) -> str:
    """Prefer FastAPI's effective template so labels include mounted prefixes.

    ``scope["route"].path`` is router-local (for example ``/health/live``), while
    the effective context carries ``/internal/v1/health/live``. The fallback keeps
    this middleware working if FastAPI changes that private context.
    """

    fastapi_context = scope.get("fastapi")
    if isinstance(fastapi_context, dict):
        effective = fastapi_context.get("effective_route_context")
        path_format = getattr(effective, "path_format", None)
        if isinstance(path_format, str) and path_format:
            return path_format
    route = scope.get("route")
    for attribute in ("path_format", "path"):
        value = getattr(route, attribute, None)
        if isinstance(value, str) and value:
            return value
    return "<unresolved>"


class HttpMetricsMiddleware:
    """Count every HTTP request, including auth rejections and unrouted calls."""

    def __init__(self, app: ASGIApp, metrics: HttpMetrics) -> None:
        self.app = app
        self.metrics = metrics

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started_at = time.perf_counter_ns()
        status_code: int | None = None
        self.metrics.begin()

        async def send_with_status(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, send_with_status)
        finally:
            self.metrics.finish(
                _route_template(scope),
                status_code,
                max(0, time.perf_counter_ns() - started_at),
            )
