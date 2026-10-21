"""Structured console logging with trace injection."""

from __future__ import annotations

import json
import logging
import socket
import sys
import threading
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from src.common.observability.context import get_trace_id

if TYPE_CHECKING:
    from src.config import ProfileSettings

_STRUCTURED_FIELDS = (
    "method",
    "route",
    "status_code",
    "duration_ms",
    "response_bytes",
    "completion",
    "outcome",
    "source",
    "actor_kind",
    "actor_id",
    "tenant_id",
    "workspace_id",
    "response_code",
    "error_code",
    "exception_type",
)
_configure_lock = threading.Lock()


class TraceContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "trace_id"):
            record.trace_id = get_trace_id()
        if not hasattr(record, "event"):
            record.event = "log"
        return True


class TextFormatter(logging.Formatter):
    converter = staticmethod(lambda timestamp: datetime.fromtimestamp(timestamp, UTC).timetuple())

    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        fields = " ".join(
            f"{name}={getattr(record, name)}"
            for name in _STRUCTURED_FIELDS
            if getattr(record, name, None) is not None
        )
        return f"{rendered} {fields}" if fields else rendered


class JsonFormatter(logging.Formatter):
    def __init__(self, service_name: str, instance_name: str) -> None:
        super().__init__()
        self.service_name = service_name
        self.instance_name = instance_name

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "service": self.service_name,
            "instance": self.instance_name,
            "logger": record.name,
            "event": getattr(record, "event", "log"),
            "message": record.getMessage(),
            "trace_id": getattr(record, "trace_id", ""),
        }
        for name in _STRUCTURED_FIELDS:
            value = getattr(record, name, None)
            if value is not None:
                payload[name] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":"))


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def setup_logging(settings: ProfileSettings) -> None:
    """Install one idempotent stdout handler owned by memory-profile."""

    level = getattr(logging, settings.log_level)
    instance_name = socket.gethostname() or "unknown"
    with _configure_lock:
        root = logging.getLogger()
        root.setLevel(level)
        for existing in list(root.handlers):
            if getattr(existing, "_memory_profile_handler", False):
                root.removeHandler(existing)
                existing.close()

        handler = logging.StreamHandler(sys.stdout)
        handler._memory_profile_handler = True  # type: ignore[attr-defined]
        handler.setLevel(level)
        handler.addFilter(TraceContextFilter())
        if settings.log_format == "json":
            handler.setFormatter(JsonFormatter(settings.service_name, instance_name))
        else:
            handler.setFormatter(
                TextFormatter(
                    "%(asctime)sZ level=%(levelname)s service="
                    f"{settings.service_name} instance={instance_name} "
                    "logger=%(name)s event=%(event)s "
                    "trace_id=%(trace_id)s message=%(message)s",
                    datefmt="%Y-%m-%dT%H:%M:%S",
                )
            )
        root.addHandler(handler)

        for name in ("httpx", "httpcore", "elastic_transport"):
            logging.getLogger(name).setLevel(logging.WARNING)
        uvicorn_access = logging.getLogger("uvicorn.access")
        uvicorn_access.handlers.clear()
        uvicorn_access.propagate = False
        uvicorn_access.disabled = True
        uvicorn_error = logging.getLogger("uvicorn.error")
        uvicorn_error.handlers.clear()
        uvicorn_error.propagate = True
