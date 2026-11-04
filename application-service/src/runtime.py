"""Process-level ownership of application service infrastructure."""

from __future__ import annotations

import asyncio

from .config import AppServiceSettings
from .db import DatabaseManager
from .infrastructure import RedisManager
from .infrastructure.upstream import MonolithUpstream
from .plugins import get_plugin
from .protocols.market_gateway import (
    MARKET_GATEWAY_FACTORY,
    DisabledMarketGateway,
    MarketGateway,
)


def build_market_gateway(settings: AppServiceSettings) -> MarketGateway:
    """Pick the market port implementation from settings.

    The community edition has no market; switch 3 then fails closed with `6020`
    while the tenant resource center (switches 1/2) keeps working.

    `http` mode resolves the implementation from the enterprise plugin registry:
    the HTTP client is enterprise code and lives in enterprise-extensions, so
    this module never imports it. Asking for `http` without the package is a
    configuration error and fails at startup instead of on the first submission.
    """

    if settings.application_service_market_mode != "http":
        return DisabledMarketGateway()
    factory = get_plugin(MARKET_GATEWAY_FACTORY)
    if factory is None:
        raise RuntimeError(
            "APPLICATION_SERVICE_MARKET_MODE=http but no market gateway is "
            "registered; install the 'enterprise-extensions' package "
            "(not available in open-source builds)"
        )
    return factory(settings)


class ProcessRuntime:
    """Own all lazy resources for exactly one operating-system process."""

    def __init__(self, settings: AppServiceSettings) -> None:
        self.settings = settings
        self.database = DatabaseManager(settings)
        self.redis = RedisManager(settings)
        self.upstream = MonolithUpstream.from_settings(settings)
        self.market = build_market_gateway(settings)

    def _close_sync_resources(self) -> None:
        self.market.close()
        self.upstream.close()
        self.redis.close_sync()
        self.database.close_sync()

    async def aclose(self) -> None:
        errors: list[Exception] = []
        for close in (
            self.redis.aclose,
            self.database.aclose_async,
            self.upstream.aclose,
        ):
            try:
                await close()
            except Exception as exc:
                errors.append(exc)
        try:
            await asyncio.to_thread(self._close_sync_resources)
        except Exception as exc:
            errors.append(exc)
        if errors:
            raise errors[0]


_process_runtime: ProcessRuntime | None = None


def set_process_runtime(runtime: ProcessRuntime) -> None:
    """Record the process runtime at assembly time.

    Deep workflow-runtime code (engine/node modules carried over from the
    monolith) resolves database/Redis handles without a FastAPI request, the
    same way the monolith reads its module-level singletons. ``create_app``
    publishes the runtime here so those call sites keep working.
    """

    global _process_runtime
    _process_runtime = runtime


def get_process_runtime() -> ProcessRuntime:
    """Return the process runtime recorded by ``create_app``; fail closed when
    the application has not been assembled."""

    if _process_runtime is None:
        raise RuntimeError("process runtime is not initialized; create_app must run first")
    return _process_runtime
