"""Knowledge error serialization and explicit legacy HTTP compatibility."""

from __future__ import annotations

import logging
from collections.abc import Mapping

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from ..errors import KnowledgeError
from ..i18n import normalize_locale, resolve_locale, translate
from ..request_logging import request_route_template
from ..trace import TRACE_ID_HEADER, get_trace_id
from .schemas.common import fail

logger = logging.getLogger(__name__)
_HTTP_ERRORS = {
    400: "KB_VALIDATION_ERROR",
    401: "KB_HTTP_UNAUTHORIZED",
    403: "KB_HTTP_FORBIDDEN",
    404: "KB_RESOURCE_NOT_FOUND",
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
# The actual endpoint identity is independent of router prefixes and query strings.
# ChunkRetrieve is the only public request schema with rerank fields today.
_VALIDATION_CONTRACTS = {
    ("src.api.routes.chunk", "retrieve_chunks"): "KB_RETRIEVAL_REQUEST_INVALID",
}
_RERANK_FIELDS = frozenset({"rerank_mode", "rerank_weights"})


def request_locale(request: Request) -> str:
    selected = getattr(request.state, "knowledge_locale", None)
    if normalize_locale(selected):
        return selected
    selected = resolve_locale(
        request.query_params.get("lang"),
        request.headers.get("Accept-Language"),
        getattr(request.state, "language", None),
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
) -> JSONResponse:
    response = JSONResponse(
        status_code=status_code,
        headers=dict(headers or {}),
        content=fail(code=response_code, msg=message, error=message),
    )
    response.headers[TRACE_ID_HEADER] = getattr(request.state, "trace_id", get_trace_id())
    response.headers["Content-Language"] = request_locale(request)
    vary = response.headers.get("Vary", "")
    if "*" not in vary and "accept-language" not in {v.strip().lower() for v in vary.split(",")}:
        response.headers["Vary"] = f"{vary}, Accept-Language" if vary else "Accept-Language"
    return response


def render_error(request: Request, error: KnowledgeError) -> JSONResponse:
    """Use one localized message for both public message fields."""
    return _response(
        request,
        status_code=error.status_code,
        response_code=error.response_code,
        message=translate(error.code, request_locale(request), error.params),
    )


def register_error_handlers(application: FastAPI) -> None:
    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        logger.warning(
            "Knowledge request validation failed route=%s trace_id=%s",
            request_route_template(request.scope),
            getattr(request.state, "trace_id", get_trace_id()),
        )
        endpoint = request.scope.get("endpoint")
        identity = (getattr(endpoint, "__module__", ""), getattr(endpoint, "__name__", ""))
        code = _VALIDATION_CONTRACTS.get(identity)
        if code is None:
            return await request_validation_exception_handler(request, exc)
        if any(
            any(part in _RERANK_FIELDS for part in error.get("loc", ())) for error in exc.errors()
        ):
            code = "KB_RERANK_CONFIG_INVALID"
        return render_error(request, KnowledgeError.from_code(code))

    @application.exception_handler(KnowledgeError)
    async def knowledge_error_handler(request: Request, exc: KnowledgeError):
        logger.warning(
            "Knowledge request failed internal_code=%s response_code=%s status=%s "
            "route=%s trace_id=%s retryable=%s",
            exc.code,
            exc.response_code,
            exc.status_code,
            request_route_template(request.scope),
            getattr(request.state, "trace_id", get_trace_id()),
            exc.retryable,
        )
        return render_error(request, exc)

    @application.exception_handler(HTTPException)
    async def http_error_handler(request: Request, exc: HTTPException):
        # Framework details may contain arbitrary application or provider data.
        key = _HTTP_ERRORS.get(exc.status_code, "KB_INTERNAL_ERROR")
        return _response(
            request,
            status_code=exc.status_code,
            response_code=exc.status_code,
            message=translate(key, request_locale(request)),
            headers=exc.headers,
        )

    @application.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception):
        logger.exception("Unhandled knowledge service error type=%s", type(exc).__name__)
        return render_error(request, KnowledgeError.from_code("KB_INTERNAL_ERROR"))
