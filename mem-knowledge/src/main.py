"""FastAPI application entrypoint for the knowledge service."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .api.router import internal_v1_router
from .bootstrap import get_settings
from .config import KnowledgeSettings
from .errors import KnowledgeError
from .logging import setup_logging
from .runtime import ProcessRuntime
from .trace import TRACE_ID_HEADER, TraceIdMiddleware, get_trace_id

logger = logging.getLogger(__name__)


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

    application = FastAPI(
        title="MemoryBear Knowledge Service",
        description="Internal MemoryBear knowledge service API",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    application.state.runtime = runtime
    application.add_middleware(TraceIdMiddleware)
    application.include_router(internal_v1_router)

    @application.exception_handler(KnowledgeError)
    async def knowledge_error_handler(
        request: Request,
        exc: KnowledgeError,
    ) -> JSONResponse:
        trace_id = getattr(request.state, "trace_id", get_trace_id())
        return JSONResponse(
            status_code=exc.status_code,
            headers={TRACE_ID_HEADER: trace_id},
            content={
                "code": exc.code,
                "message": exc.message,
                "retryable": exc.retryable,
                "trace_id": trace_id,
            },
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
            content={
                "code": "KB_INTERNAL_ERROR",
                "message": "Internal knowledge service error",
                "retryable": False,
                "trace_id": trace_id,
            },
        )

    return application


app = create_app()
