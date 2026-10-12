"""One safe completion log per HTTP request without buffering bodies."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger("memory.access")


def _route_template(scope: Scope) -> str:
    route = getattr(scope.get("route"), "path", None)
    return route if isinstance(route, str) and route else "<unresolved>"


def _state(scope: Scope) -> dict[str, Any]:
    state = scope.get("state")
    return state if isinstance(state, dict) else {}


def _level(status_code: int, completion: str) -> int:
    if completion in {"cancelled", "error"} or status_code >= 500:
        return logging.ERROR
    if completion == "closed_early" or status_code >= 400:
        return logging.WARNING
    return logging.INFO


def record_request_failure(
    request: Any,
    *,
    response_code: int | None,
    error_code: str,
) -> None:
    """Attach bounded failure metadata for the outer completion record."""

    request.state.memory_failure = {
        "response_code": response_code,
        "error_code": error_code,
    }


class RequestLoggingMiddleware:
    """ASGI access logger shared by all current and future Memory modules."""

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
        exception_type: str | None = None

        async def send_with_metrics(message: Message) -> None:
            nonlocal status_code, response_bytes, response_complete
            await send(message)
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
            elif message["type"] == "http.response.body":
                response_bytes += len(message.get("body", b""))
                if not message.get("more_body", False):
                    response_complete = True

        try:
            await self.app(scope, receive, send_with_metrics)
            if response_complete:
                completion = "complete"
        except asyncio.CancelledError:
            completion = "cancelled"
            exception_type = "CancelledError"
            raise
        except Exception as exc:
            completion = "error"
            exception_type = type(exc).__name__
            raise
        finally:
            state = _state(scope)
            final_status = (
                status_code if status_code is not None else (500 if completion == "error" else 0)
            )
            principal = state.get("memory_context")
            failure = state.get("memory_failure")
            outcome = (
                "failure"
                if failure or final_status >= 400 or completion != "complete"
                else "success"
            )
            extra = {
                "event": "request_completed",
                "trace_id": state.get("trace_id", "<unresolved>"),
                "method": scope.get("method", "<unknown>"),
                "route": _route_template(scope),
                "status_code": final_status,
                "duration_ms": max(
                    0,
                    (time.perf_counter_ns() - started_at) // 1_000_000,
                ),
                "response_bytes": response_bytes,
                "completion": completion,
                "outcome": outcome,
                "source": getattr(principal, "source", None),
                "actor_id": getattr(principal, "actor_id", None),
                "tenant_id": getattr(principal, "tenant_id", None),
                "workspace_id": getattr(principal, "workspace_id", None),
                "response_code": failure.get("response_code") if failure else None,
                "error_code": failure.get("error_code") if failure else None,
                "exception_type": exception_type,
            }
            logger.log(_level(final_status, completion), "request completed", extra=extra)
