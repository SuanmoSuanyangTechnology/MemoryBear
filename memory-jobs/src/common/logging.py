"""Structured console logging for worker and beat processes."""

from __future__ import annotations

import json
import logging
import socket
import sys
import threading
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from src.common.context import get_task_id

if TYPE_CHECKING:
    from src.config import JobsSettings

_STRUCTURED_FIELDS = (
    "event",
    "queue",
    "task_name",
    "task_id",
    "tenant_id",
    "workspace_id",
    "error_code",
    "exception_type",
)
_configure_lock = threading.Lock()


class TaskContextFilter(logging.Filter):
    """Attach the current Celery task id to every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "task_id"):
            record.task_id = get_task_id()
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
            if getattr(record, name, None) not in (None, "")
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
        }
        for name in _STRUCTURED_FIELDS:
            value = getattr(record, name, None)
            if value not in (None, ""):
                payload[name] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":"))


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def setup_logging(settings: JobsSettings, *, replace_existing: bool = False) -> None:
    """Install one idempotent stdout handler owned by memory-jobs.

    Celery installs its own handlers when a worker or beat starts, so the
    ``after_setup_logger``/``after_setup_task_logger`` hooks call this with
    ``replace_existing=True`` to keep a single, structured output format.
    """

    level = getattr(logging, settings.log_level)
    instance_name = socket.gethostname() or "unknown"
    with _configure_lock:
        root = logging.getLogger()
        root.setLevel(level)
        for existing in list(root.handlers):
            if replace_existing or getattr(existing, "_memory_jobs_handler", False):
                root.removeHandler(existing)
                existing.close()

        handler = logging.StreamHandler(sys.stdout)
        handler._memory_jobs_handler = True  # type: ignore[attr-defined]
        handler.setLevel(level)
        handler.addFilter(TaskContextFilter())
        if settings.log_format == "json":
            handler.setFormatter(JsonFormatter(settings.service_name, instance_name))
        else:
            handler.setFormatter(
                TextFormatter(
                    "%(asctime)sZ level=%(levelname)s service="
                    f"{settings.service_name} instance={instance_name} "
                    "logger=%(name)s event=%(event)s task_id=%(task_id)s "
                    "message=%(message)s",
                    datefmt="%Y-%m-%dT%H:%M:%S",
                )
            )
        root.addHandler(handler)

        # These libraries are chatty at INFO and drown out task logs.
        for name in ("elastic_transport", "urllib3", "neo4j"):
            logging.getLogger(name).setLevel(logging.WARNING)
