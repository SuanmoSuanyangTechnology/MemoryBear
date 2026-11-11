"""Knowledge error serialization and explicit legacy HTTP compatibility."""

from __future__ import annotations

from collections.abc import Mapping

from error_core.diagnostics import format_diagnostics
from error_core.knowledge import validation_diagnostic
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from mem_storage import StorageError
from starlette.exceptions import HTTPException

from ..error_mapping import map_storage_error
from ..errors import KnowledgeError
from ..i18n import normalize_locale, resolve_locale, translate
from ..request_logging import log_request_failure
from ..trace import TRACE_ID_HEADER, get_trace_id
from .schemas.common import fail

_HTTP_ERRORS = {
    400: "KB_REQUEST_MALFORMED",
    401: "KB_HTTP_UNAUTHORIZED",
    403: "KB_HTTP_FORBIDDEN",
    404: "KB_ROUTE_NOT_FOUND",
    405: "KB_HTTP_METHOD_NOT_ALLOWED",
    409: "KB_CONFLICT",
    413: "KB_HTTP_TOO_LARGE",
    422: "KB_HTTP_VALIDATION_ERROR",
    429: "KB_HTTP_RATE_LIMITED",
    500: "KB_INTERNAL_ERROR",
    502: "KB_HTTP_BAD_GATEWAY",
    503: "KB_HTTP_UNAVAILABLE",
    504: "KB_HTTP_TIMEOUT",
}


def request_locale(request: Request) -> str:
    selected = getattr(request.state, "knowledge_locale", None)
    if normalize_locale(selected):
        return selected
    selected = resolve_locale(
        explicit=request.headers.get("X-Language-Type"),
        accepted=request.headers.get("Accept-Language"),
    )
    request.state.knowledge_locale = selected
    return selected


def _response(
    request: Request,
    *,
    status_code: int,
    response_code: int,
    message: str,
    headers: Mapping[str, str] | None = None,
    diagnostic: str = "",
) -> JSONResponse:
    response = JSONResponse(
        status_code=status_code,
        headers=dict(headers or {}),
        content=fail(code=response_code, msg=message, error=diagnostic),
    )
    response.headers[TRACE_ID_HEADER] = getattr(request.state, "trace_id", get_trace_id())
    response.headers["Content-Language"] = request_locale(request)
    vary = response.headers.get("Vary", "")
    if "*" not in vary:
        for header in ("X-Language-Type", "Accept-Language"):
            if header.lower() not in {v.strip().lower() for v in vary.split(",")}:
                vary = f"{vary}, {header}" if vary else header
        response.headers["Vary"] = vary
    return response


def render_error(
    request: Request,
    error: KnowledgeError,
    *,
    exception: BaseException | None = None,
    validation_errors: list[dict] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """Log every business failure, including direct HTTP-200 failure responses."""
    log_request_failure(
        request,
        status_code=error.status_code,
        response_code=error.response_code,
        error_code=error.code,
        message=translate(error.code, "en", error.params),
        params=dict(error.params),
        retryable=error.retryable,
        exception=exception if exception is not None else error,
        validation_errors=validation_errors,
    )
    return _response(
        request,
        status_code=error.status_code,
        response_code=error.response_code,
        message=translate(error.code, request_locale(request)),
        headers=headers,
        diagnostic=format_diagnostics(
            trace_id=getattr(request.state, "trace_id", get_trace_id()),
            detail=(
                validation_diagnostic(validation_errors)
                if validation_errors
                else error.diagnostic or f"cause={error.code}"
            ),
        ),
    )


def register_error_handlers(application: FastAPI) -> None:
    @application.exception_handler(StorageError)
    async def storage_error_handler(request: Request, exc: StorageError):
        return render_error(request, map_storage_error(exc))

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        code = (
            "KB_REQUEST_MALFORMED"
            if any(error["type"] == "json_invalid" for error in exc.errors())
            else "KB_HTTP_VALIDATION_ERROR"
        )
        return render_error(
            request, KnowledgeError.from_code(code), exception=exc, validation_errors=exc.errors()
        )

    @application.exception_handler(KnowledgeError)
    async def knowledge_error_handler(request: Request, exc: KnowledgeError):
        return render_error(request, exc)

    @application.exception_handler(HTTPException)
    async def http_error_handler(request: Request, exc: HTTPException):
        key = _HTTP_ERRORS.get(exc.status_code, "KB_INTERNAL_ERROR")
        status = {502: 500, 504: 503}.get(exc.status_code, exc.status_code)
        error = KnowledgeError.from_code(key, status_code=status)
        return render_error(request, error, exception=exc, headers=exc.headers)

    @application.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception):
        return render_error(request, KnowledgeError.from_code("KB_INTERNAL_ERROR"), exception=exc)
