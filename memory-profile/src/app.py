"""FastAPI application factory and module composition."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI

from src.bootstrap import get_settings
from src.common.observability import setup_logging
from src.common.redis_cache import clear_redis_cache, configure_redis_cache
from src.config import ProfileSettings
from src.error_handlers import register_error_handlers
from src.middleware.auth import (
    MemoryAuthConfig,
    MemoryAuthMiddleware,
    load_gateway_auth,
)
from src.middleware.request_logging import RequestLoggingMiddleware
from src.middleware.trace import TraceIdMiddleware
from src.modules.analytics.routes import router as analytics_router
from src.modules.health.routes import router as health_router
from src.runtime import ProfileRuntime


def create_app(
    settings: ProfileSettings | None = None,
    runtime: ProfileRuntime | None = None,
) -> FastAPI:
    """Build the application without connecting to external infrastructure."""

    resolved_settings = settings or get_settings()
    setup_logging(resolved_settings)
    process_runtime = runtime or ProfileRuntime(resolved_settings)
    configure_redis_cache(process_runtime.redis.client)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            clear_redis_cache()
            await process_runtime.aclose()

    application = FastAPI(
        title=resolved_settings.service_name,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    application.state.settings = resolved_settings
    application.state.runtime = process_runtime

    auth_config = MemoryAuthConfig(
        auth_mode=resolved_settings.memory_auth_mode,
        service_name=resolved_settings.memory_auth_service_name,
        header_prefix=resolved_settings.memory_auth_header_prefix,
        public_paths=frozenset(
            {
                "/internal/v1/health/live",
                "/internal/v1/health/ready",
            }
        ),
        kill_switch_file=resolved_settings.memory_auth_kill_switch_file or None,
        jwks_url=resolved_settings.memory_auth_jwks_url or None,
        redis=process_runtime.redis.client,
    )
    gateway_auth = (
        load_gateway_auth(auth_config) if resolved_settings.memory_auth_mode == "gateway" else None
    )
    # Starlette executes the last registered middleware first. Trace must be
    # outermost so authentication rejections always receive an X-Trace-Id.
    application.add_middleware(
        MemoryAuthMiddleware,
        config=auth_config,
        gateway_auth=gateway_auth,
    )
    application.add_middleware(TraceIdMiddleware)
    if resolved_settings.access_log_enabled:
        application.add_middleware(RequestLoggingMiddleware)

    internal_router = APIRouter(prefix="/internal/v1")
    internal_router.include_router(health_router)
    internal_router.include_router(analytics_router)
    application.include_router(internal_router)
    register_error_handlers(application)
    return application
