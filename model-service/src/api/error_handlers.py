"""Legacy-compatible error serialization for the migrated management API.

Every handler reproduces the host ``app/main.py`` wire contract: the same
numeric ``code``/``msg``/``error`` envelope, the same HTTP status mapping, and
the same i18n keys — the host proxy passes these responses through verbatim.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError as PydanticValidationError
from starlette.exceptions import HTTPException

from ..errors import (
    BizCode,
    BusinessException,
    I18nException,
    as_biz_code,
    http_status_for,
)
from ..i18n import get_default_locale, translate
from ..request_logging import log_request_failure, safe_failure_detail
from ..sensitive import SensitiveDataFilter
from ..trace import TRACE_ID_HEADER, get_trace_id
from .schemas.common import fail

logger = logging.getLogger(__name__)

_HTTP_ERROR_KEYS = {
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

_VALIDATION_ERROR_KEYS = {
    "value_error.missing": "errors.validation.missing_field",
    "value_error.any_str.max_length": "errors.validation.field_too_long",
    "value_error.any_str.min_length": "errors.validation.field_too_short",
}
_VALIDATION_FALLBACK_KEY = "errors.validation.invalid_field"


def _request_locale(request: Request) -> str:
    selected = getattr(request.state, "language", None)
    return selected if isinstance(selected, str) and selected else get_default_locale()


def _response(
    request: Request,
    *,
    status_code: int,
    content: dict,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    response = JSONResponse(status_code=status_code, content=content, headers=headers)
    response.headers[TRACE_ID_HEADER] = getattr(request.state, "trace_id", get_trace_id())
    return response


def render_error(
    request: Request,
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

    log_request_failure(
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
    return _response(request, status_code=status_code, content=content)


def register_error_handlers(application: FastAPI) -> None:
    @application.exception_handler(BusinessException)
    async def business_error_handler(request: Request, exc: BusinessException):
        message, context = SensitiveDataFilter.filter_message(exc.message, exc.context)
        biz_code = as_biz_code(exc.code)
        status_code = http_status_for(biz_code)
        return render_error(
            request,
            status_code=status_code,
            content=fail(code=biz_code.value, msg=message, error=message),
            error_code=biz_code.name,
            message=message,
            params=context,
            retryable=status_code >= 500,
            exception=exc,
        )

    @application.exception_handler(I18nException)
    async def i18n_error_handler(request: Request, exc: I18nException):
        detail = exc.detail
        if isinstance(detail, dict):
            filtered_detail = {
                **detail,
                "message": SensitiveDataFilter.filter_string(detail.get("message", "")),
            }
        else:
            filtered_detail = SensitiveDataFilter.filter_string(str(detail))
        content = {"success": False, **filtered_detail}
        return render_error(
            request,
            status_code=exc.status_code,
            content=content,
            error_code=str(filtered_detail.get("error_code", "I18N_ERROR"))
            if isinstance(filtered_detail, dict)
            else "I18N_ERROR",
            message=str(filtered_detail.get("message", ""))
            if isinstance(filtered_detail, dict)
            else str(filtered_detail),
            exception=exc,
        )

    @application.exception_handler(RequestValidationError)
    async def request_validation_error_handler(request: Request, exc: RequestValidationError):
        detail = (
            "; ".join(
                str(error.get("msg") or "").removeprefix("Value error, ")
                for error in exc.errors()
            )
            or "请求参数校验失败"
        )
        return render_error(
            request,
            status_code=422,
            content=fail(
                code=BizCode.VALIDATION_FAILED.value, msg=detail, error=detail
            ),
            error_code="VALIDATION_FAILED",
            message=detail,
            exception=exc,
            validation_errors=exc.errors(),
        )

    @application.exception_handler(PydanticValidationError)
    async def pydantic_validation_error_handler(
        request: Request, exc: PydanticValidationError
    ):
        language = _request_locale(request)
        errors = []
        for error in exc.errors():
            field = ".".join(str(loc) for loc in error["loc"])
            error_type = error["type"]
            key = _VALIDATION_ERROR_KEYS.get(error_type, _VALIDATION_FALLBACK_KEY)
            errors.append(
                {
                    "field": field,
                    "message": translate(key, language, field=field),
                    "type": error_type,
                }
            )
        message = translate("errors.common.validation_failed", language)
        return render_error(
            request,
            status_code=422,
            content={
                "success": False,
                "error_code": "VALIDATION_FAILED",
                "message": message,
                "errors": errors,
            },
            error_code="VALIDATION_FAILED",
            message=message,
            exception=exc,
            validation_errors=exc.errors(),
        )

    @application.exception_handler(HTTPException)
    async def http_error_handler(request: Request, exc: HTTPException):
        language = _request_locale(request)
        key = _HTTP_ERROR_KEYS.get(exc.status_code)
        if key is not None:
            message = translate(key, language)
        else:
            message = SensitiveDataFilter.filter_string(str(exc.detail))
        return render_error(
            request,
            status_code=exc.status_code,
            content=fail(code=exc.status_code, msg=message, error=exc.detail),
            error_code=f"HTTP_{exc.status_code}",
            message=message,
            retryable=exc.status_code >= 500,
            exception=exc,
        )

    @application.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception):
        environment = getattr(request.app.state.runtime.settings, "environment", "development")
        if environment == "production":
            message = translate("errors.common.internal_error", _request_locale(request))
        else:
            message = SensitiveDataFilter.filter_string(str(exc))
        return render_error(
            request,
            status_code=500,
            content=fail(code=BizCode.INTERNAL_ERROR.value, msg=message, error=message),
            error_code="INTERNAL_ERROR",
            message=safe_failure_detail(message, "Internal server error"),
            retryable=True,
            exception=exc,
        )
