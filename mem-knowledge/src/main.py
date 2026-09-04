"""FastAPI application entrypoint for the knowledge service."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .api.router import internal_v1_router
from .api.schemas.common import ApiResponse, fail
from .bootstrap import get_settings
from .config import KnowledgeSettings
from .errors import KnowledgeError
from .logging import setup_logging
from .request_logging import RequestLoggingFastAPI, request_route_template
from .runtime import ProcessRuntime
from .trace import TRACE_ID_HEADER, TraceIdMiddleware, get_trace_id

logger = logging.getLogger(__name__)

_HTTP_MESSAGES = {
    "zh": {
        400: "请求参数错误",
        401: "未授权访问",
        403: "没有权限访问",
        404: "请求的资源不存在",
        405: "不支持的请求方法",
        409: "资源冲突",
        422: "数据验证失败",
        429: "请求过于频繁，请稍后再试",
        500: "服务器内部错误",
        503: "服务暂时不可用",
    },
    "en": {
        400: "Bad request parameters",
        401: "Unauthorized access",
        403: "Access forbidden",
        404: "Resource not found",
        405: "Method not allowed",
        409: "Resource conflict",
        422: "Validation failed",
        429: "Too many requests, please try again later",
        500: "Internal server error",
        503: "Service temporarily unavailable",
    },
}

_LEGACY_ERROR_RESPONSES = {
    status_code: {
        "model": ApiResponse[Any],
        "description": "Legacy-compatible error response",
    }
    for status_code in (400, 404, 409, 500)
}
_RERANK_VALIDATION_FIELDS = frozenset({"rerank_mode", "rerank_weights"})
_RERANK_VALIDATION_MESSAGE = "Invalid rerank configuration"
_RETRIEVAL_VALIDATION_MESSAGE = "Invalid retrieval request"


def _request_language(request: Request) -> str:
    requested = request.query_params.get("lang", "").lower()
    if requested.startswith("en"):
        return "en"
    accepted = request.headers.get("Accept-Language", "").lower()
    return "en" if accepted.startswith("en") else "zh"


def _http_message(request: Request, status_code: int, detail: object) -> str:
    language = _request_language(request)
    return _HTTP_MESSAGES[language].get(status_code, str(detail))


def _is_rerank_validation_error(exc: RequestValidationError) -> bool:
    return any(
        any(part in _RERANK_VALIDATION_FIELDS for part in error.get("loc", ()))
        for error in exc.errors()
    )


def create_app(settings: KnowledgeSettings | None = None) -> FastAPI:
    """Construct the internal API without connecting to dependencies."""

    service_settings = settings or get_settings()
    setup_logging(service_settings)
    runtime = ProcessRuntime(service_settings)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "Knowledge service started: %s",
            service_settings.safe_summary(),
        )
        try:
            yield
        finally:
            await application.state.runtime.aclose()
            logger.info("Knowledge service stopped")

    application = RequestLoggingFastAPI(
        title="MemoryBear Knowledge Service",
        description="Internal MemoryBear knowledge service API",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        responses=_LEGACY_ERROR_RESPONSES,
        lifespan=lifespan,
    )
    application.state.runtime = runtime
    application.add_middleware(TraceIdMiddleware)
    # 鉴权最外层（后注册者最外层）：无凭据请求在路由前即 401
    from .auth import KbAuthConfig, KbAuthMiddleware

    application.add_middleware(
        KbAuthMiddleware,
        kb_auth=KbAuthConfig(
            auth_mode=service_settings.kb_auth_mode,
            service_name=service_settings.kb_service_name,
            kill_switch_file=service_settings.kb_kill_switch_file,
            jwks_url=service_settings.kb_jwks_url,
            secret=(
                service_settings.kb_secret.get_secret_value()
                if service_settings.kb_secret is not None
                else None
            ),
            api_key_verify_url=service_settings.kb_api_key_verify_url,
            redis=runtime.redis.client,
        ),
    )
    application.include_router(internal_v1_router)

    @application.exception_handler(RequestValidationError)
    async def request_validation_error_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        trace_id = getattr(request.state, "trace_id", get_trace_id())
        route_path = request_route_template(request.scope)
        validation_errors = [
            {
                "loc": str((tuple(error.get("loc", ())) or ("unknown",))[0]),
                "type": str(error.get("type", "")),
                "msg": "Request validation failed",
            }
            for error in exc.errors()
        ]
        logger.error(
            "Knowledge request validation failed trace_id=%s method=%s path=%s "
            "actor_id=%s tenant_id=%s workspace_id=%s source=%s errors=%s",
            trace_id,
            request.method,
            route_path,
            request.headers.get("X-KB-Actor-ID"),
            request.headers.get("X-KB-Tenant-ID"),
            request.headers.get("X-KB-Workspace-ID"),
            request.headers.get("X-KB-Source"),
            validation_errors,
        )
        if request.url.path.endswith("/chunks/retrieval"):
            return JSONResponse(
                status_code=400,
                headers={TRACE_ID_HEADER: trace_id},
                content=fail(
                    code=400,
                    msg=_http_message(request, 400, _RETRIEVAL_VALIDATION_MESSAGE),
                    error=_RETRIEVAL_VALIDATION_MESSAGE,
                ),
            )
        if _is_rerank_validation_error(exc):
            return JSONResponse(
                status_code=400,
                headers={TRACE_ID_HEADER: trace_id},
                content=fail(
                    code=400,
                    msg=_http_message(request, 400, _RERANK_VALIDATION_MESSAGE),
                    error=_RERANK_VALIDATION_MESSAGE,
                ),
            )
        return await request_validation_exception_handler(request, exc)

    @application.exception_handler(KnowledgeError)
    async def knowledge_error_handler(
        request: Request,
        exc: KnowledgeError,
    ) -> JSONResponse:
        trace_id = getattr(request.state, "trace_id", get_trace_id())
        logger.warning(
            "Knowledge request failed internal_code=%s response_code=%s status=%s"
            " path=%s method=%s trace_id=%s retryable=%s",
            exc.code,
            exc.response_code,
            exc.status_code,
            request.url.path,
            request.method,
            trace_id,
            exc.retryable,
        )
        if exc.response_style == "business":
            message = exc.message
            error = exc.message
        elif exc.response_style == "internal":
            message = _http_message(request, 500, exc.message)
            error = message
        else:
            message = _http_message(request, exc.status_code, exc.message)
            error = exc.message
        return JSONResponse(
            status_code=exc.status_code,
            headers={TRACE_ID_HEADER: trace_id},
            content=fail(
                code=exc.response_code,
                msg=message,
                error=error,
            ),
        )

    @application.exception_handler(StarletteHTTPException)
    async def http_error_handler(
        request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        trace_id = getattr(request.state, "trace_id", get_trace_id())
        return JSONResponse(
            status_code=exc.status_code,
            headers={TRACE_ID_HEADER: trace_id, **(exc.headers or {})},
            content=fail(
                code=exc.status_code,
                msg=_http_message(request, exc.status_code, exc.detail),
                error=exc.detail,
            ),
        )

    @application.exception_handler(Exception)
    async def unhandled_error_handler(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        trace_id = getattr(request.state, "trace_id", get_trace_id())
        logger.exception(
            "Unhandled knowledge service error type=%s",
            type(exc).__name__,
        )
        return JSONResponse(
            status_code=500,
            headers={TRACE_ID_HEADER: trace_id},
            content=fail(
                code=10001,
                msg=_http_message(request, 500, exc),
                error=_http_message(request, 500, exc),
            ),
        )

    return application


app = create_app()
