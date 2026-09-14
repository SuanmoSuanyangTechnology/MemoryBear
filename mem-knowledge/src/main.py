"""FastAPI application entrypoint for the knowledge service."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from .api.error_handlers import register_error_handlers
from .api.router import internal_v1_router
from .api.schemas.common import ApiResponse
from .bootstrap import get_settings
from .config import KnowledgeSettings
from .i18n import load_catalogs
from .logging import setup_logging
from .request_logging import RequestLoggingFastAPI
from .runtime import ProcessRuntime
from .trace import TraceIdMiddleware

logger = logging.getLogger(__name__)

_LEGACY_ERROR_RESPONSES = {
    status_code: {
        "model": ApiResponse[Any],
        "description": "Legacy-compatible error response",
    }
    for status_code in (400, 404, 409, 500)
}


def create_app(settings: KnowledgeSettings | None = None) -> FastAPI:
    """Construct the internal API without connecting to dependencies."""

    load_catalogs()
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

    register_error_handlers(application)

    return application


app = create_app()
