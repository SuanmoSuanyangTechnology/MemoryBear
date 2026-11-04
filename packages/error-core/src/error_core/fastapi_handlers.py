"""FastAPI error handlers for the unified ``code/msg/error`` envelope.

The handler logic is shared verbatim by the split services; each service binds
its own exception types, translator, logging sink, masking helpers, and trace
implementation through :class:`ErrorHandlerDeps`. This module requires
FastAPI/Starlette, which every HTTP service already installs.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError as PydanticValidationError
from starlette.exceptions import HTTPException

from .codes import BizCode, code_name, coerce_code, http_status_for
from .diagnostics import format_diagnostics
from .envelope import fail, now_ms
from .messages import fallback_message, user_message

HTTP_ERROR_KEYS = {
    400: "errors.common.bad_request",
    401: "errors.common.unauthorized",
    403: "errors.common.forbidden",
    404: "errors.common.not_found",
    405: "errors.common.method_not_allowed",
    409: "errors.common.conflict",
    413: "errors.common.payload_too_large",
    422: "errors.common.validation_failed",
    429: "errors.common.too_many_requests",
    500: "errors.common.internal_error",
    502: "errors.common.bad_gateway",
    503: "errors.common.service_unavailable",
    504: "errors.common.gateway_timeout",
}

VALIDATION_ERROR_KEYS = {
    "value_error.missing": "errors.validation.missing_field",
    "value_error.any_str.max_length": "errors.validation.field_too_long",
    "value_error.any_str.min_length": "errors.validation.field_too_short",
}
VALIDATION_FALLBACK_KEY = "errors.validation.invalid_field"


@dataclass(frozen=True, slots=True)
class ErrorHandlerDeps:
    """Service-specific bindings used by the shared handlers."""

    business_exception: type[Exception]
    i18n_exception: type[Exception]
    translate: Callable[..., str]
    get_default_locale: Callable[[], str]
    log_failure: Callable[..., None]
    filter_message: Callable[[str, dict[str, Any] | None], tuple[str, dict[str, Any]]]
    filter_string: Callable[[str], str]
    get_trace_id: Callable[[], str]
    trace_header: str = "X-Trace-Id"


def auth_rejection_content(
    http_status: int,
    detail: str,
    language: str,
    *,
    translate: Callable[..., str],
) -> dict[str, Any]:
    """The envelope HTTP and gRPC render for an authentication rejection."""

    code = BizCode.FORBIDDEN if http_status == 403 else BizCode.UNAUTHORIZED
    key = HTTP_ERROR_KEYS.get(http_status, "errors.common.unauthorized")
    return fail(
        code=code.value,
        msg=translate(key, language),
        error=format_diagnostics(detail=detail),
    )


def validation_error_content(
    exc: PydanticValidationError,
    language: str,
    *,
    translate: Callable[..., str],
    trace_id: str | None = None,
) -> dict[str, Any]:
    """Pydantic errors -> the legacy 422 envelope (HTTP handler and gRPC share it)."""

    errors = []
    for error in exc.errors():
        field = ".".join(str(loc) for loc in error["loc"])
        error_type = error["type"]
        key = VALIDATION_ERROR_KEYS.get(error_type, VALIDATION_FALLBACK_KEY)
        errors.append(
            {
                "field": field,
                "message": translate(key, language, field=field),
                "type": error_type,
            }
        )
    message = translate("errors.common.validation_failed", language)
    detail = "; ".join(
        str(error.get("msg") or "").removeprefix("Value error, ")
        for error in exc.errors()
    )
    return {
        "success": False,
        "error_code": "VALIDATION_FAILED",
        "message": message,
        "errors": errors,
        "code": BizCode.VALIDATION_FAILED.value,
        "msg": message,
        "data": {},
        "error": format_diagnostics(trace_id=trace_id, detail=detail),
        "time": now_ms(),
    }


def request_trace_id(request: Request, deps: ErrorHandlerDeps) -> str:
    return getattr(request.state, "trace_id", None) or deps.get_trace_id()


def request_locale(request: Request, deps: ErrorHandlerDeps) -> str:
    selected = getattr(request.state, "language", None)
    return selected if isinstance(selected, str) and selected else deps.get_default_locale()


def render_error(
    request: Request,
    deps: ErrorHandlerDeps,
    *,
    status_code: int,
    content: dict,
    error_code: str,
    message: str,
    params: dict | None = None,
    retryable: bool | None = None,
    exception: BaseException | None = None,
    validation_errors: list[dict] | None = None,
) -> JSONResponse:
    """Log every failure, then render the legacy envelope unchanged."""

    deps.log_failure(
        request,
        status_code=status_code,
        response_code=content.get("code"),
        error_code=error_code,
        message=message,
        params=params,
        retryable=retryable,
        exception=exception,
        validation_errors=validation_errors,
    )
    response = JSONResponse(status_code=status_code, content=content)
    response.headers[deps.trace_header] = request_trace_id(request, deps)
    return response


def register_error_handlers(application: FastAPI, deps: ErrorHandlerDeps) -> None:
    @application.exception_handler(deps.business_exception)
    async def business_error_handler(request: Request, exc: Any):
        message, context = deps.filter_message(exc.message, exc.context)
        code = coerce_code(exc.code)
        numeric = code if code is not None else BizCode.BAD_REQUEST.value
        status_code = http_status_for(numeric)
        language = request_locale(request, deps)
        display = (
            user_message(numeric, language, **context)
            or message
            or fallback_message(status_code, language)
        )
        detail = format_diagnostics(
            trace_id=getattr(request.state, "trace_id", None) or deps.get_trace_id(),
            detail=message,
        )
        return render_error(
            request,
            deps,
            status_code=status_code,
            content=fail(
                code=numeric,
                msg=display,
                error=detail,
                data=getattr(exc, "data", None),
            ),
            error_code=code_name(numeric),
            message=display,
            params=context,
            retryable=status_code >= 500,
            exception=exc,
        )

    @application.exception_handler(deps.i18n_exception)
    async def i18n_error_handler(request: Request, exc: Any):
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        message = deps.filter_string(str(detail.get("msg") or detail.get("message") or ""))
        numeric = coerce_code(detail.get("code"))
        if numeric is None:
            numeric = BizCode.BAD_REQUEST.value
        error_code = str(detail.get("error_code") or code_name(numeric))
        diagnostic = format_diagnostics(
            trace_id=getattr(request.state, "trace_id", None) or deps.get_trace_id(),
            detail=message,
        )
        content = {
            "code": numeric,
            "msg": message,
            "data": {},
            "error": diagnostic,
            "time": now_ms(),
            "message": message,  # legacy alias
            "error_code": error_code,  # legacy field
        }
        return render_error(
            request,
            deps,
            status_code=exc.status_code,
            content=content,
            error_code=error_code,
            message=message,
            exception=exc,
        )

    @application.exception_handler(RequestValidationError)
    async def request_validation_error_handler(request: Request, exc: RequestValidationError):
        # Same fallback literal as the host handler (wire parity).
        detail = (
            "; ".join(
                str(error.get("msg") or "").removeprefix("Value error, ")
                for error in exc.errors()
            )
            or "请求参数校验失败"
        )
        language = request_locale(request, deps)
        message = deps.translate("errors.common.validation_failed", language)
        diagnostic = format_diagnostics(
            trace_id=getattr(request.state, "trace_id", None) or deps.get_trace_id(),
            detail=detail,
        )
        return render_error(
            request,
            deps,
            status_code=422,
            content=fail(
                code=BizCode.VALIDATION_FAILED.value, msg=message, error=diagnostic
            ),
            error_code="VALIDATION_FAILED",
            message=message,
            exception=exc,
            validation_errors=exc.errors(),
        )

    @application.exception_handler(PydanticValidationError)
    async def pydantic_validation_error_handler(
        request: Request, exc: PydanticValidationError
    ):
        content = validation_error_content(
            exc,
            request_locale(request, deps),
            translate=deps.translate,
            trace_id=getattr(request.state, "trace_id", None) or deps.get_trace_id(),
        )
        return render_error(
            request,
            deps,
            status_code=422,
            content=content,
            error_code="VALIDATION_FAILED",
            message=content["message"],
            exception=exc,
            validation_errors=exc.errors(),
        )

    @application.exception_handler(HTTPException)
    async def http_error_handler(request: Request, exc: HTTPException):
        language = request_locale(request, deps)
        key = HTTP_ERROR_KEYS.get(exc.status_code)
        message = (
            deps.translate(key, language)
            if key is not None
            else fallback_message(exc.status_code, language)
        )
        diagnostic = format_diagnostics(
            trace_id=getattr(request.state, "trace_id", None) or deps.get_trace_id(),
            detail=str(exc.detail),
        )
        return render_error(
            request,
            deps,
            status_code=exc.status_code,
            content=fail(code=exc.status_code, msg=message, error=diagnostic),
            error_code=f"HTTP_{exc.status_code}",
            message=message,
            retryable=exc.status_code >= 500,
            exception=exc,
        )

    @application.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception):
        environment = getattr(
            request.app.state.runtime.settings, "environment", "development"
        )
        language = request_locale(request, deps)
        message = deps.translate("errors.common.internal_error", language)
        raw = str(exc) if environment != "production" else type(exc).__name__
        diagnostic = format_diagnostics(
            trace_id=getattr(request.state, "trace_id", None) or deps.get_trace_id(),
            detail=raw,
        )
        return render_error(
            request,
            deps,
            status_code=500,
            content=fail(
                code=BizCode.INTERNAL_ERROR.value, msg=message, error=diagnostic
            ),
            error_code="INTERNAL_ERROR",
            message=message,
            retryable=True,
            exception=exc,
        )


__all__ = [
    "HTTP_ERROR_KEYS",
    "VALIDATION_ERROR_KEYS",
    "VALIDATION_FALLBACK_KEY",
    "ErrorHandlerDeps",
    "auth_rejection_content",
    "register_error_handlers",
    "render_error",
    "request_locale",
    "request_trace_id",
    "validation_error_content",
]
