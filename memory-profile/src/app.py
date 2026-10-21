"""FastAPI application factory and module composition."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.bootstrap import get_settings
from src.common.external_api import verify_external_api_contract
from src.common.observability import setup_logging
from src.common.observability.runtime_metrics import start_tracemalloc
from src.common.redis_cache import clear_redis_cache, configure_redis_cache
from src.config import ProfileSettings
from src.error_handlers import register_error_handlers
from src.i18n import LanguageMiddleware, clear_i18n, configure_i18n
from src.middleware.auth import (
    MemoryAuthConfig,
    MemoryAuthMiddleware,
    load_gateway_auth,
)
from src.middleware.metrics import HttpMetricsMiddleware
from src.middleware.request_logging import RequestLoggingMiddleware
from src.middleware.trace import TraceIdMiddleware
from src.modules import router as modules_router
from src.runtime import ProfileRuntime

logger = logging.getLogger(__name__)


def create_app(
    settings: ProfileSettings | None = None,
    runtime: ProfileRuntime | None = None,
) -> FastAPI:
    """Build the application without connecting to external infrastructure."""

    resolved_settings = settings or get_settings()
    setup_logging(resolved_settings)
    start_tracemalloc(resolved_settings)
    process_runtime = runtime or ProfileRuntime(resolved_settings)
    configure_redis_cache(process_runtime.redis.client)
    configure_i18n(
        locales_dir=resolved_settings.i18n_locales_dir,
        default_language=resolved_settings.i18n_default_language,
        fallback_language=resolved_settings.i18n_fallback_language,
        supported_languages=resolved_settings.i18n_language_list,
    )

    auth_config = MemoryAuthConfig(
        auth_mode=resolved_settings.memory_auth_mode,
        service_name=resolved_settings.memory_auth_service_name,
        public_paths=frozenset(
            {
                "/internal/v1/health/live",
                "/internal/v1/health/ready",
                "/internal/v1/health/metrics",
            }
        ),
        kill_switch_file=resolved_settings.memory_auth_kill_switch_file or None,
        jwks_url=resolved_settings.memory_auth_jwks_url or None,
        redis=process_runtime.redis.client,
        acl_rules_key=resolved_settings.acl_rules_key_name,
        acl_rules_changed_channel=resolved_settings.acl_rules_changed_channel_name,
    )
    # Built once here so the middleware and the lifespan share the same handler
    # (the lifespan owns its ACL rule-refresh task).
    gateway_auth = (
        load_gateway_auth(auth_config) if resolved_settings.memory_auth_mode == "gateway" else None
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # A failed refresh start degrades hot reload only; channel-1 auth keeps
        # the rules loaded so far, so startup must not be blocked by it.
        if gateway_auth is not None and hasattr(gateway_auth, "start"):
            try:
                await gateway_auth.start()
            except Exception:
                logger.exception("memory gateway auth refresh start failed")
        try:
            yield
        finally:
            # Stop the subscription task before the redis pool closes.
            if gateway_auth is not None and hasattr(gateway_auth, "aclose"):
                await gateway_auth.aclose()
            clear_redis_cache()
            clear_i18n()
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
    application.state.http_metrics = process_runtime.http_metrics

    # Starlette executes the last registered middleware first. With access
    # logging enabled the resulting order is:
    # RequestLogging -> TraceId -> Language -> Metrics -> MemoryAuth -> routing.
    # Trace must sit outside Auth so authentication rejections always carry an
    # X-Trace-Id; Metrics must sit outside Auth so rejected requests are counted.
    application.add_middleware(
        MemoryAuthMiddleware,
        config=auth_config,
        gateway_auth=gateway_auth,
    )
    application.add_middleware(
        HttpMetricsMiddleware,
        metrics=process_runtime.http_metrics,
    )
    # Resolve the language before authentication so auth failures are localized too.
    application.add_middleware(LanguageMiddleware)
    application.add_middleware(TraceIdMiddleware)
    if resolved_settings.access_log_enabled:
        application.add_middleware(RequestLoggingMiddleware)

    application.include_router(modules_router)
    # Public (API-key reachable) endpoints are an external contract: compare
    # their request parameters with the committed snapshot once, at startup.
    verify_external_api_contract(modules_router.routes)
    register_error_handlers(application)
    return application
