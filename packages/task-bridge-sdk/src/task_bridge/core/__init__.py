"""Mechanism-neutral contracts: bridge facade, transport interface, status, errors."""

from task_bridge.core.bridge import TaskBridge
from task_bridge.core.errors import (
    BridgeConfigurationError,
    BridgeError,
    InvalidTaskPayloadError,
    TaskFailedError,
    TaskPublishError,
    TaskResultUnavailableError,
    TaskTimeoutError,
)
from task_bridge.core.status import READY_STATES, TERMINAL_STATES, TaskState, TaskStatus
from task_bridge.core.transport import (
    TaskSubmission,
    TaskTransport,
    TaskWatch,
    ThreadedTaskTransport,
)

__all__ = [
    "READY_STATES",
    "TERMINAL_STATES",
    "BridgeConfigurationError",
    "BridgeError",
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
]
