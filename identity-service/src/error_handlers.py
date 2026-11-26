"""Wire-exact error serialization for the migrated management API.

``error_core`` supplies the envelope, code coercion, validation handlers and
diagnostics. Handlers whose v0.5.5 defaults would change the monolith wire
(BusinessException headers/copy, raw HTTPException detail, environment-gated
500 copy) are overridden here; the I18nException shape follows the unified
v0.5.5 contract on purpose (recorded in the cutover divergence table).
"""

from __future__ import annotations

import logging
from typing import Any

from error_core.codes import (
    HTTP_STATUS_TO_BIZ_CODE,
    BizCode,
    coerce_code,
    http_status_for,
)
from error_core.diagnostics import format_diagnostics
from error_core.envelope import fail
from error_core.fastapi_handlers import HTTP_ERROR_KEYS, ErrorHandlerDeps
from error_core.fastapi_handlers import register_error_handlers as _register_shared
from error_core.messages import user_message
from fastapi import FastAPI, Request
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from src.config import settings
from src.core.exceptions import BusinessException, I18nException, PermissionDeniedException
from src.i18n import get_current_locale, get_default_locale, translate
from src.sensitive import SensitiveDataFilter
from src.trace import TRACE_ID_HEADER, get_trace_id

logger = logging.getLogger(__name__)


def _request_language(request: Request) -> str:
    language = getattr(request.state, "language", None)
    if isinstance(language, str) and language:
        return language
    return get_current_locale() or get_default_locale()


def _request_trace_id(request: Request) -> str:
    return getattr(request.state, "trace_id", None) or get_trace_id()


def _log_failure(
    request: Request,
    *,
    status_code: int,
    response_code: Any,
    error_code: str,
    message: str,
    params: dict[str, Any] | None,
    retryable: bool | None,
    exception: BaseException | None,
    validation_errors: list[dict] | None,
) -> None:
    log = logger.error if status_code >= 500 else logger.warning
    log(
        "request failed path=%s method=%s status=%s code=%s error_code=%s message=%s trace_id=%s",
        request.url.path,
        request.method,
        status_code,
        response_code,
        error_code,
        message,
        _request_trace_id(request),
        exc_info=exception if status_code >= 500 else None,
    )


_deps = ErrorHandlerDeps(
    business_exception=BusinessException,
    i18n_exception=I18nException,
    translate=translate,
    get_default_locale=get_default_locale,
    log_failure=_log_failure,
    filter_message=SensitiveDataFilter.filter_message,
    filter_string=SensitiveDataFilter.filter_string,
    get_trace_id=get_trace_id,
    trace_header=TRACE_ID_HEADER,
)


def register_error_handlers(application: FastAPI) -> None:
    # RequestValidationError / PydanticValidationError / I18nException and the
    # default handlers come from the shared module.
    _register_shared(application, _deps)

    @application.exception_handler(BusinessException)
    async def business_error_handler(request: Request, exc: BusinessException) -> JSONResponse:
        filtered_message, filtered_context = SensitiveDataFilter.filter_message(
            exc.message, exc.context
        )
        numeric = coerce_code(exc.code)
        if numeric is None:
            numeric = BizCode.BAD_REQUEST.value
        language = _request_language(request)
        logger.error(
            "Business error: %s path=%s method=%s context=%s code=%s code_name=%s "
            "msg_locale=%s trace_id=%s",
            filtered_message,
            request.url.path,
            request.method,
            filtered_context,
            numeric,
            BizCode(numeric).name if numeric in BizCode._value2member_map_ else numeric,
            language,
            _request_trace_id(request),
            exc_info=exc.cause if exc.cause is not None else None,
        )
        display = user_message(numeric, language) or filtered_message
        detail = format_diagnostics(
            trace_id=_request_trace_id(request), detail=filtered_message
        )
        response = JSONResponse(
            status_code=http_status_for(numeric),
            content=fail(numeric, display, error=detail),
        )
        # Context headers such as WWW-Authenticate are part of the auth contract.
        for header_name, header_value in (exc.context.get("headers") or {}).items():
            response.headers[header_name] = str(header_value)
        for header_name, header_value in (
            exc.context.get("rate_limit_headers") or {}
        ).items():
            response.headers[header_name] = str(header_value)
        return response

    @application.exception_handler(PermissionDeniedException)
    async def permission_denied_handler(
        request: Request, exc: PermissionDeniedException
    ) -> JSONResponse:
        filtered_message, filtered_context = SensitiveDataFilter.filter_message(
            exc.message, exc.context
        )
        logger.warning(
            "Permission denied: %s path=%s method=%s context=%s trace_id=%s",
            filtered_message,
            request.url.path,
            request.method,
            filtered_context,
            _request_trace_id(request),
        )
        biz_code = exc.code if isinstance(exc.code, BizCode) else BizCode.FORBIDDEN
        return JSONResponse(
            status_code=http_status_for(biz_code),
            content=fail(code=biz_code.value, msg=filtered_message, error=filtered_message),
        )

    @application.exception_handler(HTTPException)
    async def http_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
        language = _request_language(request)
        key = HTTP_ERROR_KEYS.get(exc.status_code)
        message = (
            translate(key, language)
            if key is not None
            else SensitiveDataFilter.filter_string(str(exc.detail))
        )
        biz_code = HTTP_STATUS_TO_BIZ_CODE.get(exc.status_code)
        if biz_code is None:
            biz_code = (
                BizCode.INTERNAL_ERROR
                if exc.status_code >= 500
                else BizCode.BAD_REQUEST
            )
        logger.warning(
            "HTTP exception: %s path=%s method=%s status=%s trace_id=%s",
            message,
            request.url.path,
            request.method,
            exc.status_code,
            _request_trace_id(request),
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=fail(code=biz_code.value, msg=message, error=exc.detail),
            headers=exc.headers,
        )

    @application.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        language = _request_language(request)
        logger.error(
            "Unhandled exception: %s path=%s method=%s exception_type=%s trace_id=%s",
            exc,
            request.url.path,
            request.method,
            type(exc).__name__,
            _request_trace_id(request),
            exc_info=True,
        )
        if settings.ENVIRONMENT == "production":
            message = translate("errors.common.internal_error", language)
        else:
            message = SensitiveDataFilter.filter_string(str(exc))
        # This handler runs in ServerErrorMiddleware, outside TraceIdMiddleware,
        # so the trace header must be stamped here (same as error_core render_error).
        return JSONResponse(
            status_code=500,
            content=fail(code=BizCode.INTERNAL_ERROR.value, msg=message, error=message),
            headers={TRACE_ID_HEADER: _request_trace_id(request)},
        )


__all__ = ["register_error_handlers"]
