"""Concrete transports; resolved lazily so the core install stays Celery-free."""

from importlib import import_module

__all__ = ["BridgeConfig", "CeleryBridge", "CeleryConfig", "CeleryTransport"]


def __getattr__(name: str):
    if name in __all__:
        try:
            celery_transports = import_module("task_bridge.transports.celery")
        except ImportError as exc:  # pragma: no cover - depends on install extras
            raise ImportError(
                "Celery support is not installed; install task-bridge-sdk[celery]"
            ) from exc
        return getattr(celery_transports, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
