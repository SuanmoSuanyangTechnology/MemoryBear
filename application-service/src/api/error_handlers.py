"""Legacy-compatible error serialization for the migrated management API.

The handler logic lives in ``error_core.fastapi_handlers``; this module binds
it to the application service's exception types, translator, logging sink,
masking helpers, and trace implementation.
"""

from __future__ import annotations

from typing import Any

from error_core.fastapi_handlers import (
    ErrorHandlerDeps,
)
from error_core.fastapi_handlers import (
    auth_rejection_content as _auth_rejection_content,
)
from error_core.fastapi_handlers import (
    register_error_handlers as _register_error_handlers,
)
from error_core.fastapi_handlers import (
    render_error as _render_error,
)
from error_core.fastapi_handlers import (
    validation_error_content as _validation_error_content,
)
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError as PydanticValidationError

from ..errors import BusinessException, I18nException
from ..i18n import get_default_locale, translate
from ..request_logging import log_request_failure
from ..sensitive import SensitiveDataFilter
from ..trace import TRACE_ID_HEADER, get_trace_id

_deps = ErrorHandlerDeps(
    business_exception=BusinessException,
    i18n_exception=I18nException,
    translate=translate,
    get_default_locale=get_default_locale,
    log_failure=log_request_failure,
    filter_message=SensitiveDataFilter.filter_message,
    filter_string=SensitiveDataFilter.filter_string,
    get_trace_id=get_trace_id,
    trace_header=TRACE_ID_HEADER,
)


def auth_rejection_content(
    http_status: int, detail: str, language: str
) -> dict[str, Any]:
    """The envelope HTTP and gRPC render for an authentication rejection."""

    return _auth_rejection_content(
        http_status, detail, language, translate=translate
    )


def validation_error_content(
    exc: PydanticValidationError,
    language: str,
    *,
    trace_id: str | None = None,
) -> dict[str, Any]:
    """Pydantic errors -> the legacy 422 envelope (HTTP handler and gRPC share it)."""

    return _validation_error_content(
        exc, language, translate=translate, trace_id=trace_id
    )


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

    return _render_error(
        request,
        _deps,
        status_code=status_code,
        content=content,
        error_code=error_code,
        message=message,
        params=params,
        retryable=retryable,
        exception=exception,
        validation_errors=validation_errors,
    )


def register_error_handlers(application: FastAPI) -> None:
    _register_error_handlers(application, _deps)


__all__ = [
    "auth_rejection_content",
    "register_error_handlers",
    "render_error",
    "validation_error_content",
]
