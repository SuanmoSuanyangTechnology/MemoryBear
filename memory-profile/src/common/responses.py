"""Generic API response envelopes and error-response emitters.

Three layers, in order of use:

1. ``success`` / ``failure`` build the ``ApiResponse`` model.
2. ``error_response`` emits any error body (standard envelope or legacy shape)
   as a JSONResponse, records the failure for the access log, and writes the
   trace header.
3. ``http_error_response`` / ``server_error_response`` / ``validation_error_response``
   are the legacy-contract presets; they only choose the body, never the
   plumbing.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from src.common.errors import BizCode
from src.common.observability.context import record_request_failure
from src.common.timing import now_ms
from src.i18n import get_request_language, t

_HTTP_MESSAGE_KEYS = {
    400: "common.http.bad_request",
    404: "common.http.not_found",
    500: "common.http.internal_error",
}


def _resolve_language(request: Request, language: str | None) -> str:
    """Use the caller's language, otherwise the middleware negotiation."""

    return language if language is not None else get_request_language(request)


class ApiResponse[DataT](BaseModel):
    code: int = 0
    msg: str = "OK"
    data: DataT
    error: str = ""
    time: int = Field(default_factory=now_ms)


def success(data: Any, msg: str = "OK") -> ApiResponse[Any]:
    """Build the standard success envelope."""

    return ApiResponse[Any](data=data, msg=msg)


def failure(
    code: int,
    msg: str,
    error: str = "",
    data: Any = None,
) -> ApiResponse[Any]:
    """Build an error model for explicit JSONResponse boundaries."""

    return ApiResponse[Any](
        code=code,
        msg=msg,
        data={} if data is None else data,
        error=error,
    )


def error_response(
    request: Request,
    status_code: int,
    body: ApiResponse[Any] | dict[str, Any],
    *,
    response_code: int | None = None,
    error_code: str = "REQUEST_REJECTED",
) -> JSONResponse:
    """Emit an error body and record the failure for the access log.

    ``body`` is either the standard ``failure(...)`` envelope or a legacy dict
    shape; ``response_code`` defaults to ``body.code`` for envelopes and to
    ``status_code`` for dicts. Recording the failure is what lets the access log
    show ``outcome=failure`` even when the HTTP status is 200, which the legacy
    memory-config endpoints use for business errors.
    """

    if isinstance(body, ApiResponse):
        content = body.model_dump(mode="json")
        resolved_code = body.code if response_code is None else response_code
    else:
        content = body
        resolved_code = status_code if response_code is None else response_code
    record_request_failure(
        request,
        response_code=resolved_code,
        error_code=error_code,
    )
    response = JSONResponse(status_code=status_code, content=content)
    trace_id = getattr(request.state, "trace_id", None)
    if trace_id:
        response.headers["X-Trace-Id"] = trace_id
    return response


def http_error_response(
    request: Request,
    status_code: int,
    detail: str,
    language: str | None = None,
    *,
    code: int | None = None,
) -> JSONResponse:
    """Preset: the monolith's ``HTTPException`` envelope.

    ``code`` echoes the HTTP status unless overridden (the catch-all uses
    ``10001``), ``msg`` is the shared ``common.http.*`` text, and ``detail``
    keeps the raw diagnostic.
    """

    key = _HTTP_MESSAGE_KEYS.get(status_code, "common.http.internal_error")
    return error_response(
        request,
        status_code,
        failure(
            status_code if code is None else code,
            t(key, _resolve_language(request, language)),
            detail,
        ),
    )


def server_error_response(
    request: Request,
    detail: str,
    language: str | None = None,
) -> JSONResponse:
    """Preset: HTTP 500 catch-all with the legacy business code ``10001``."""

    return http_error_response(
        request,
        500,
        detail,
        language,
        code=BizCode.INTERNAL_ERROR,
    )


def validation_error_response(
    request: Request,
    exc: ValidationError,
    language: str | None = None,
) -> JSONResponse:
    """Preset: the standard 422 envelope for a Pydantic validation error.

    ``msg`` is the localized request-validation text; ``error`` keeps the joined
    field messages for diagnostics.
    """

    language = _resolve_language(request, language)
    detail = "; ".join(
        str(error.get("msg") or "").removeprefix("Value error, ") for error in exc.errors()
    ) or t("common.validation.request_failed", language)
    return error_response(
        request,
        422,
        failure(
            BizCode.VALIDATION_FAILED,
            t("common.validation.request_failed", language),
            detail,
        ),
    )
