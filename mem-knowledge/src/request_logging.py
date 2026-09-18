"""HTTP request lifecycle logging without buffering request or response bodies."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import traceback

from fastapi import FastAPI, Request
from fastapi.exceptions import ValidationException
from pydantic import ValidationError
from starlette.datastructures import Headers
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .errors import KnowledgeError
from .logging import redact_for_log
from .trace import TRACE_ID_HEADER, normalize_trace_id

logger = logging.getLogger(__name__)


def request_route_template(scope: Scope) -> str:
    route_path = getattr(scope.get("route"), "path", None)
    if not isinstance(route_path, str) or not route_path:
        return "<unresolved>"
    request_path = str(scope.get("path", ""))
    if request_path.startswith("/internal/v1/") and not route_path.startswith("/internal/v1/"):
        return f"/internal/v1{route_path}"
    return route_path


DEPENDENCY_FAILURE_RESPONSE_CODE = 10001
MAX_FAILURE_FIELDS = 20
MAX_FAILURE_VALUE_LENGTH = 1200
_SENSITIVE_FIELDS = ("password", "secret", "token", "authorization", "api_key", "api-key", "cookie")


def _bounded(value: object) -> str:
    try:
        return redact_for_log(value)[:MAX_FAILURE_VALUE_LENGTH]
    except Exception:
        return f"<unprintable {type(value).__name__}>"


def safe_failure_detail(detail: object, fallback: str) -> str:
    """Select an error reason, never a structured request/response payload."""
    if isinstance(detail, str):
        return _bounded(detail)
    if isinstance(detail, dict):
        for key in ("message", "msg", "reason", "error", "detail"):
            if isinstance(detail.get(key), str):
                return _bounded(detail[key])
    return fallback


def _validation_message(error: dict) -> str:
    if any(
        marker in str(part).lower() for part in error.get("loc", ()) for marker in _SENSITIVE_FIELDS
    ):
        return "Invalid sensitive field"
    return _bounded(error.get("msg", "Validation failed"))


def _validation_summary(errors: list[dict]) -> list[dict]:
    return [
        {
            "loc": [_bounded(part) for part in item.get("loc", ())[:MAX_FAILURE_FIELDS]],
            "type": _bounded(item.get("type", "")),
            "msg": _validation_message(item),
        }
        for item in errors[:MAX_FAILURE_FIELDS]
    ]


def _exception_nodes(exception: BaseException | None) -> list[BaseException]:
    """Follow Python chaining and exception groups without retaining them in state."""
    pending = [exception] if exception is not None else []
    nodes: list[BaseException] = []
    seen: set[int] = set()
    while pending and len(nodes) < MAX_FAILURE_FIELDS:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        nodes.append(current)
        if isinstance(current, BaseExceptionGroup):
            pending.extend(reversed(current.exceptions))
        cause = current.__cause__
        if cause is None and not current.__suppress_context__:
            cause = current.__context__
        if cause is not None:
            pending.append(cause)
    return nodes


def _sensitive_exception(exception: BaseException) -> bool:
    return isinstance(exception, (ValidationError, ValidationException)) or (
        isinstance(exception, HTTPException) and not isinstance(exception.detail, str)
    )


def _safe_exception_chain(nodes: list[BaseException]) -> list[dict]:
    result = []
    for exception in nodes:
        item = {
            "type": type(exception).__name__,
            "frames": [
                {"file": frame.f_code.co_filename, "line": line, "function": frame.f_code.co_name}
                for frame, line in list(traceback.walk_tb(exception.__traceback__))[
                    -MAX_FAILURE_FIELDS:
                ]
            ],
        }
        if isinstance(exception, (ValidationError, ValidationException)):
            errors = exception.errors()
            item["validation_errors"] = _validation_summary(errors)
            item["validation_error_count"] = len(errors)
        elif isinstance(exception, HTTPException):
            item["message"] = safe_failure_detail(exception.detail, "HTTP request failed")
        elif isinstance(exception, KnowledgeError):
            item["message"] = exception.code
        # Other context messages may repeat a validation input. Frames and type
        # locate the failure without dumping those values or source/locals.
        result.append(item)
    return result


def log_request_failure(
    request: Request,
    *,
    status_code: int,
    response_code: int | None,
    error_code: str,
    message: str,
    params: dict | None = None,
    retryable: bool | None = None,
    exception: BaseException | None = None,
    validation_errors: list[dict] | None = None,
) -> None:
    """Record actionable failure evidence without inspecting HTTP bodies."""
    trace_id = getattr(request.state, "trace_id", None)
    if not trace_id:
        trace_id = normalize_trace_id(request.headers.get(TRACE_ID_HEADER))
        request.state.trace_id = trace_id
    request.state.knowledge_failure = {
        "response_code": response_code,
        "error_code": error_code,
        "message": _bounded(message),
    }
    context = {
        "method": request.method,
        "route": request_route_template(request.scope),
        "path_params": {
            key: _bounded(value)
            for key, value in list(request.path_params.items())[:MAX_FAILURE_FIELDS]
        },
        "status": status_code,
        "response_code": response_code,
        "internal_code": error_code,
        "trace_id": trace_id,
        "message": _bounded(message),
        "params": params or {},
        "retryable": retryable,
    }
    if context["route"] == "<unresolved>":
        context["path"] = _bounded(request.url.path)
    if validation_errors is not None:
        context["validation_errors"] = _validation_summary(validation_errors)
        context["validation_error_count"] = len(validation_errors)
    nodes = _exception_nodes(exception)
    chain_truncated = len(nodes) >= MAX_FAILURE_FIELDS
    sensitive_chain = (
        validation_errors is not None
        or chain_truncated
        or any(_sensitive_exception(e) for e in nodes)
    )
    if chain_truncated:
        context["exception_chain_truncated"] = True
    if nodes:
        context["exception_type"] = type(nodes[0]).__name__
        if sensitive_chain:
            context["exception_chain"] = _safe_exception_chain(nodes)
        else:
            context["exception_message"] = _bounded(nodes[0])
            if len(nodes) > 1:
                context["cause_type"] = type(nodes[1]).__name__
                context["cause_message"] = _bounded(nodes[1])
    dependency_failure = status_code >= 500 or response_code == DEPENDENCY_FAILURE_RESPONSE_CODE
    with_stack = (
        exception is not None and not sensitive_chain and (dependency_failure or len(nodes) > 1)
    )
    try:
        logger.log(
            logging.ERROR if dependency_failure else logging.WARNING,
            "request_failed %s",
            json.dumps(context, ensure_ascii=False, default=str),
            exc_info=(type(exception), exception, exception.__traceback__) if with_stack else None,
            extra={"trace_id": trace_id},
        )
        request.state.knowledge_logged_exception_ids = tuple(id(item) for item in nodes)
    except Exception as error:
        # Logging is observational: a broken custom sink must not replace the
        # intended business response or recursively fail the error handler.
        request.state.failure_logging_error = type(error).__name__


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
        unreported_error: BaseException | None = None
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
        except Exception as exc:
            completion = "error"
            reported = scope.get("state", {}).get("knowledge_logged_exception_ids", ())
            if id(exc) not in reported:
                unreported_error = exc
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
            failure = scope.get("state", {}).get("knowledge_failure")
            transport_nodes = _exception_nodes(unreported_error)
            sensitive_transport = len(transport_nodes) >= MAX_FAILURE_FIELDS or any(
                _sensitive_exception(item) for item in transport_nodes
            )
            transport_diagnostics = (
                _safe_exception_chain(transport_nodes) if sensitive_transport else None
            )
            outcome = (
                "failure"
                if failure or final_status >= 400 or completion != "complete"
                else "success"
            )
            level = _completion_level(final_status, completion)
            if failure:
                level = max(level, logging.WARNING)
            try:
                logger.log(
                    level,
                    "request_completed service=mem-knowledge trace_id=%s method=%s "
                    "route=%s status=%s duration_ms=%s response_bytes=%s completion=%s "
                    "actor_id=%s tenant_id=%s workspace_id=%s source=%s "
                    "outcome=%s response_code=%s internal_code=%s "
                    "failure_reason=%s log_sink_error=%s transport_diagnostics=%s",
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
                    outcome,
                    failure.get("response_code") if failure else None,
                    failure.get("error_code") if failure else None,
                    failure.get("message") if failure else None,
                    scope.get("state", {}).get("failure_logging_error"),
                    transport_diagnostics,
                    exc_info=(
                        type(unreported_error),
                        unreported_error,
                        unreported_error.__traceback__,
                    )
                    if unreported_error is not None and not sensitive_transport
                    else None,
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
    "log_request_failure",
    "safe_failure_detail",
]
