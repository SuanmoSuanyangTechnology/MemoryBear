"""Transport-neutral task bridge with an optional Celery implementation.

The core abstraction (``TaskBridge``, ``TaskTransport``, ``TaskStatus`` ...)
has no third-party dependencies. The Celery names are resolved lazily so a
base install imports cleanly; installing ``task-bridge-sdk[celery]`` makes
them available.
"""

from __future__ import annotations

from importlib import import_module

from task_bridge.core import (
    READY_STATES,
    TERMINAL_STATES,
    BridgeConfigurationError,
    BridgeError,
    InvalidTaskPayloadError,
    TaskBridge,
    TaskFailedError,
    TaskPublishError,
    TaskResultUnavailableError,
    TaskState,
    TaskStatus,
    TaskSubmission,
    TaskTimeoutError,
    TaskTransport,
    TaskWatch,
    ThreadedTaskTransport,
)

__version__ = "0.1.0"

_CELERY_EXPORTS = frozenset(
    {"BridgeConfig", "CeleryBridge", "CeleryConfig", "CeleryTransport"}
)


def __getattr__(name: str):
    if name in _CELERY_EXPORTS:
        try:
            celery_transports = import_module("task_bridge.transports.celery")
        except ImportError as exc:  # pragma: no cover - depends on install extras
            raise ImportError(
                "Celery support is not installed; install task-bridge-sdk[celery]"
            ) from exc
        return getattr(celery_transports, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "READY_STATES",
    "TERMINAL_STATES",
    "BridgeConfig",
    "BridgeConfigurationError",
    "BridgeError",
    "CeleryBridge",
    "CeleryConfig",
    "CeleryTransport",
    "InvalidTaskPayloadError",
    "TaskBridge",
    "TaskFailedError",
    "TaskPublishError",
    "TaskResultUnavailableError",
    "TaskState",
    "TaskStatus",
    "TaskSubmission",
    "TaskTimeoutError",
    "TaskTransport",
    "TaskWatch",
    "ThreadedTaskTransport",
    "__version__",
]
