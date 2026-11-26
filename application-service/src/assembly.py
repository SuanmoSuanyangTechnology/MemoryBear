"""Process-level assembly shared by the HTTP app and the Celery worker."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .bootstrap import get_settings
from .config import AppServiceSettings, configure_settings
from .i18n import configure as configure_i18n
from .i18n import load_catalogs
from .logging import setup_logging
from .protocols.market_gateway import DisabledMarketGateway, MarketGateway
from .runtime import ProcessRuntime, set_process_runtime
from .sensitive import SensitiveDataFilter

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AssembledProcess:
    settings: AppServiceSettings
    runtime: ProcessRuntime


def register_enterprise_market() -> None:
    """Register the community market's enterprise implementation.

    Must run **before** ``ProcessRuntime`` is constructed: the market gateway is
    resolved from the plugin registry by name (``MARKET_GATEWAY_FACTORY``), and
    `http` mode without it is a startup error rather than a per-request 500.
    Open-source builds cannot install the package, so the import failure is
    expected and leaves the registry empty (switch 3 then fails closed with 6020).
    """
    try:
        from enterprise_ext.app import register_app_market
    except ImportError:
        return
    register_app_market()


def register_enterprise_plugins(runtime: ProcessRuntime) -> None:
    """Register enterprise plugins when the private package is installed.

    Community deployments must start without a License and keep the community
    context path, so the private hook receives the deployment mode and skips
    the License-gated plugins itself. Open-source builds cannot install
    enterprise-extensions, so the import failure is expected and leaves the
    community plugin registry empty.
    """
    try:
        from enterprise_ext.app import register_app_plugins
    except ImportError:
        return
    register_app_plugins(
        runtime.upstream, deployment_mode=runtime.settings.deployment_mode
    )


def load_market_gateway(runtime: ProcessRuntime) -> MarketGateway:
    settings = runtime.settings
    if settings.application_service_market_mode == "disabled":
        return DisabledMarketGateway()
    try:
        from enterprise_ext.app_market import build_market_gateway
    except ImportError as exc:
        raise RuntimeError(
            "APPLICATION_SERVICE_MARKET_MODE=gateway requires the private "
            "'enterprise-extensions' package (not installed in open-source builds)"
        ) from exc
    return build_market_gateway(settings)


def assemble_process(settings: AppServiceSettings | None = None) -> AssembledProcess:
    """Configure settings and publish the process runtime for this process.

    Both entrypoints call this exactly once per process: ``create_app`` for the
    HTTP server, ``worker_process_init`` for every forked Celery child (the
    parent only imports task modules).
    """

    load_catalogs()
    service_settings = settings or get_settings()
    setup_logging(service_settings)
    SensitiveDataFilter.configure(service_settings)
    configure_i18n(service_settings.i18n_default_language)
    configure_settings(service_settings)
    # Enterprise market wiring first: `ProcessRuntime` resolves the market gateway
    # from the plugin registry, and route registration must precede mounting.
    register_enterprise_market()
    runtime = ProcessRuntime(service_settings)
    # Deep workflow-runtime code (carried over from the monolith) resolves
    # process resources through this module-level registration.
    set_process_runtime(runtime)
    register_enterprise_plugins(runtime)
    runtime.market_gateway = load_market_gateway(runtime)
    return AssembledProcess(settings=service_settings, runtime=runtime)
