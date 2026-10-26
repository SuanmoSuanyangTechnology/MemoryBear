"""FastAPI application entrypoint for the application service."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api.error_handlers import register_error_handlers
from .api.router import internal_v1_router
from .api.schemas.response_schema import ApiResponse
from .auth import AppAuthConfig, AppAuthMiddleware, _load_gateway_auth
from .bootstrap import get_settings
from .config import AppServiceSettings, configure_settings
from .i18n import LanguageContextMiddleware, load_catalogs
from .i18n import configure as configure_i18n
from .integrations.knowledge.runtime import (
    close_knowledge_integration,
    initialize_knowledge_integration,
)
from .integrations.sandbox.runtime import (
    close_sandbox_integration,
    initialize_sandbox_integration,
)
from .logging import setup_logging
from .request_logging import RequestLoggingFastAPI
from .runtime import ProcessRuntime, set_process_runtime
from .sensitive import SensitiveDataFilter
from .services.batch_persist_queue import BatchPersistQueue
from .trace import TraceIdMiddleware

logger = logging.getLogger(__name__)

_LEGACY_ERROR_RESPONSES = {
    status_code: {
        "model": ApiResponse,
        "description": "Legacy-compatible error response",
    }
    for status_code in (400, 401, 404, 409, 422, 500)
}


def create_app(settings: AppServiceSettings | None = None) -> FastAPI:
    """Construct the internal API without connecting to dependencies."""

    load_catalogs()
    service_settings = settings or get_settings()
    setup_logging(service_settings)
    SensitiveDataFilter.configure(service_settings)
    configure_i18n(service_settings.i18n_default_language)
    configure_settings(service_settings)
    runtime = ProcessRuntime(service_settings)
    # Deep workflow-runtime code (carried over from the monolith) resolves
    # process resources through this module-level registration.
    set_process_runtime(runtime)

    app_auth_config = AppAuthConfig(
        auth_mode=service_settings.application_service_auth_mode,
        service_name=service_settings.application_service_internal_name,
        jwks_url=service_settings.application_service_jwks_url,
        redis=runtime.redis.client,
        acl_rules_key=service_settings.acl_rules_key_name,
        acl_rules_changed_channel=service_settings.acl_rules_changed_channel_name,
    )
    gateway_auth = None
    if app_auth_config.auth_mode == "gateway":
        try:
            # Built once here so the middleware and the lifespan share the same
            # handler (the lifespan owns its rule-refresh task).
            gateway_auth = _load_gateway_auth(app_auth_config)
        except RuntimeError:
            # Misconfiguration (private package missing): the forced
            # build_middleware_stack() below re-raises through the middleware's
            # lazy fallback, so app construction still fails loudly.
            logger.exception("gateway auth handler unavailable")

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        logger.info("Application service started: %s", service_settings.safe_summary())
        # A failed refresh start degrades hot reload only; auth keeps the rules
        # loaded so far, so startup must not be blocked by it.
        if gateway_auth is not None:
            try:
                await gateway_auth.start()
            except Exception:
                logger.exception("gateway auth refresh start failed")
        # Knowledge retriever selection is process-level: the workflow node and
        # draft runs resolve it lazily, so it must exist before serving traffic.
        await initialize_knowledge_integration()
        # The sandbox service client pool is process-level too. Rule selection is
        # explicit: present only when ENABLE_SANDBOX_SERVICE=true and the base URL
        # / token validate, so an enabled-but-misconfigured deployment fails
        # startup instead of failing each tool call.
        await initialize_sandbox_integration()
        # Post-stream persistence runs in the background; enqueue calls degrade
        # to sync writes while the queue is down, so this is not fail-closed.
        await BatchPersistQueue.start()
        try:
            yield
        finally:
            if gateway_auth is not None:
                await gateway_auth.aclose()
            # Drain queued persistence before the database engine is disposed.
            await BatchPersistQueue.flush()
            await BatchPersistQueue.stop()
            await close_knowledge_integration()
            await close_sandbox_integration()
            await application.state.runtime.aclose()
            logger.info("Application service stopped")

    application = RequestLoggingFastAPI(
        title="MemoryBear Application Service",
        description="Internal MemoryBear application service API",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        responses=_LEGACY_ERROR_RESPONSES,
        lifespan=lifespan,
    )
    application.state.runtime = runtime
    application.add_middleware(TraceIdMiddleware)
    # Auth runs before routing (later registrations sit further out): a request
    # without internal credentials is rejected with 401 before business handling.
    application.add_middleware(
        AppAuthMiddleware,
        app_auth=app_auth_config,
        gateway=gateway_auth,
    )
    # Locale resolution is outermost: even auth-phase 401 responses carry the
    # correct Content-Language (auth resolves the lang header internally).
    application.add_middleware(LanguageContextMiddleware)
    application.include_router(internal_v1_router)

    register_error_handlers(application)

    # The stack is normally built on the first request; build it here so a
    # gateway-mode handler assembly failure (missing enterprise-extensions
    # package) surfaces at startup instead of becoming a per-request 500.
    application.middleware_stack = application.build_middleware_stack()

    return application


app = create_app()
