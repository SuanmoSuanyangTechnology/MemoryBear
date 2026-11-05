"""Unified legacy-envelope error handling."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.common.errors import BizCode, LocalizedProfileError, ProfileError
from src.common.responses import ApiResponse, error_response, failure, validation_error_response
from src.i18n import get_request_language, t

logger = logging.getLogger(__name__)


def _response(request: Request, status: int, body: ApiResponse[Any]) -> JSONResponse:
    return error_response(request, status, body)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ProfileError)
    async def profile_error(request: Request, exc: ProfileError) -> JSONResponse:
        message = exc.message
        detail = exc.detail
        if isinstance(exc, LocalizedProfileError):
            language = get_request_language(request)
            message = t(exc.message_key, language, **exc.message_params)
            if exc.detail_key:
                detail = t(exc.detail_key, language, **exc.detail_params)
        return _response(
            request,
            exc.status_code,
            failure(exc.code, message, detail, exc.data),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return validation_error_response(request, exc)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _response(
            request,
            exc.status_code,
            failure(BizCode.INVALID_PARAMETER, str(exc.detail)),
        )

    @app.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled profile service error", exc_info=exc)
        return _response(
            request,
            500,
            failure(
                BizCode.INTERNAL_ERROR,
                t("common.internal_error", get_request_language(request)),
            ),
        )
