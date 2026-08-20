"""Console logging with trace injection and credential redaction."""

from __future__ import annotations

import logging
import re
import threading
from typing import Any

from .config import KnowledgeSettings
from .trace import get_trace_id

_URL_CREDENTIAL = re.compile(r"(://[^:/\s]+:)[^@\s]+(@)")
_SECRET_VALUE = re.compile(
    r"(?i)\b(password|secret|token|api[_-]?key|authorization)"
    r"(\s*(?:[=:]|\bis\b)\s*)([^\s,;]+)"
)
_configure_lock = threading.Lock()


def redact_for_log(value: object) -> str:
    text = str(value)
    text = _URL_CREDENTIAL.sub(r"\1***\2", text)
    return _SECRET_VALUE.sub(r"\1\2***", text)


def _redact_argument(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "***"
            if any(marker in str(key).lower() for marker in ("password", "secret", "token", "key"))
            else _redact_argument(item)
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return tuple(_redact_argument(item) for item in value)
    if isinstance(value, list):
        return [_redact_argument(item) for item in value]
    if isinstance(value, str):
        return redact_for_log(value)
    return value


class SensitiveDataFilter(logging.Filter):
    """Redact credentials before a record reaches a formatter."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_for_log(record.msg)
        if record.args:
            record.args = _redact_argument(record.args)
        return True


class TraceIdFilter(logging.Filter):
    """Attach the current trace ID to every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "trace_id"):
            record.trace_id = get_trace_id()
        return True


class RedactingFormatter(logging.Formatter):
    """Redact the final formatted message, including exception text."""

    def format(self, record: logging.LogRecord) -> str:
        return redact_for_log(super().format(record))


def setup_logging(settings: KnowledgeSettings) -> None:
    """Install one knowledge-service stdout handler."""

    with _configure_lock:
        root = logging.getLogger()
        root.setLevel(settings.kb_log_level)
        for handler in list(root.handlers):
            if getattr(handler, "_kb_handler", False):
                root.removeHandler(handler)
                handler.close()

        handler = logging.StreamHandler()
        handler._kb_handler = True  # type: ignore[attr-defined]
        handler.setLevel(settings.kb_log_level)
        handler.setFormatter(
            RedactingFormatter(
                "%(asctime)s %(levelname)s [%(trace_id)s] %(name)s %(message)s"
            )
        )
        handler.addFilter(SensitiveDataFilter())
        handler.addFilter(TraceIdFilter())
        root.addHandler(handler)

        for noisy_logger in ("httpx", "httpcore", "elastic_transport"):
            logging.getLogger(noisy_logger).setLevel(logging.WARNING)
