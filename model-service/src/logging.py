"""Console logging with trace injection and credential redaction."""

from __future__ import annotations

import json
import logging
import re
import threading
from collections.abc import Collection, Mapping
from typing import Any
from urllib.parse import unquote_plus

from .config import ModelServiceSettings
from .trace import get_trace_id

_URL_CREDENTIAL = re.compile(r"(://)[^\s/?#@]+@")
_QUERY_PARAMETER = re.compile(r"([?&])([^?&#=\s]+)=([^&#\s\"'<>\\]*)")
_CREDENTIAL_SUFFIX = re.compile(r"_(?:key|token|secret|password|auth|signature|credential)$")
_SECRET_VALUE = re.compile(
    r"""(?ix)
    (\b[a-z0-9_-]*(?:password|passwd|secret|token|api[_-]?key|authorization|cookie)[a-z0-9_-]*\b)
    (["']?\s*(?:[=:]|\bis\b)\s*)
    ("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|(?:Bearer|Basic)\s+[^\s,;"']+|[^\s,;&"'\\]+)
    """
)
_ASSIGNED_CREDENTIAL = re.compile(
    r"""(?ix)
    (\b[a-z0-9_-]*(?:signature|credential)[a-z0-9_-]*\b)
    (["']?\s*[=:]\s*)
    ("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s,;&"'\\]+)
    """
)
_BEARER_VALUE = re.compile(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+")
_configure_lock = threading.Lock()
_CREDENTIAL_FIELDS = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "secret_key",
        "api_key",
        "apikey",
        "key",
        "authorization",
        "token",
        "access_token",
        "refresh_token",
        "session_token",
        "cookie",
        "signature",
        "sig",
        "credential",
        "x_amz_signature",
        "x_amz_credential",
        "x_goog_signature",
        "x_goog_credential",
    }
)


def _is_credential_field(key: str) -> bool:
    normalized = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", unquote_plus(key))
    normalized = normalized.lower().replace("-", "_")
    return normalized in _CREDENTIAL_FIELDS or _CREDENTIAL_SUFFIX.search(normalized) is not None


def redact_for_log(value: object) -> str:
    text = str(value)
    text = _URL_CREDENTIAL.sub(r"\1***@", text)
    text = _QUERY_PARAMETER.sub(
        lambda match: f"{match[1]}{match[2]}=***" if _is_credential_field(match[2]) else match[0],
        text,
    )

    def replace(match: re.Match[str]) -> str:
        # 普通查询参数已由键名识别，不能再被旧文本规则按子串遮蔽。
        if (
            match.start()
            and match.string[match.start() - 1] in "?&"
            and not _is_credential_field(match.group(1))
        ):
            return match[0]
        value = match.group(3)
        masked = value[0] + "***" + value[0] if value[0] in "\"'" else "***"
        return match.group(1) + match.group(2) + masked

    text = _SECRET_VALUE.sub(replace, text)
    text = _ASSIGNED_CREDENTIAL.sub(
        lambda match: replace(match) if _is_credential_field(match[1]) else match[0],
        text,
    )
    return _BEARER_VALUE.sub(r"\1 ***", text)


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
        if getattr(record, "structured_diagnostic", False):
            return True
        if record.args:
            record.args = _redact_argument(record.args)
        # Interpolate before redaction so masking password=%s cannot leave an
        # unused argument and make the logging formatter drop the entire event.
        record.msg = redact_for_log(record.getMessage())
        record.args = ()
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
        if getattr(record, "structured_diagnostic", False):
            return super().format(record)
        return redact_for_log(super().format(record))


def log_diagnostic(
    target: logging.Logger,
    level: int,
    payload: Mapping[str, Any],
    *,
    secrets: Collection[str] = (),
) -> None:
    """字段脱敏后只编码一次；末端 formatter 不再改写 JSON 转义。"""

    def clean(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {
                key: "***" if _is_credential_field(str(key)) else clean(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [clean(item) for item in value]
        if isinstance(value, str):
            for secret in secrets:
                value = value.replace(secret, "***")
            return redact_for_log(value)
        return value

    target.log(
        level,
        "%s",
        json.dumps(clean(payload), ensure_ascii=False, default=str),
        extra={"structured_diagnostic": True, "trace_id": payload.get("trace_id", "")},
    )


def setup_logging(settings: ModelServiceSettings) -> None:
    """Install one model-service stdout handler."""

    with _configure_lock:
        root = logging.getLogger()
        root.setLevel(settings.model_service_log_level)
        for handler in list(root.handlers):
            if getattr(handler, "_model_service_handler", False):
                root.removeHandler(handler)
                handler.close()

        handler = logging.StreamHandler()
        handler._model_service_handler = True  # type: ignore[attr-defined]
        handler.setLevel(settings.model_service_log_level)
        handler.setFormatter(
            RedactingFormatter("%(asctime)s %(levelname)s [%(trace_id)s] %(name)s %(message)s")
        )
        handler.addFilter(SensitiveDataFilter())
        handler.addFilter(TraceIdFilter())
        root.addHandler(handler)

        for noisy_logger in ("httpx", "httpcore"):
            logging.getLogger(noisy_logger).setLevel(logging.WARNING)

        from .services.invoke_diagnostics import install_sdk_failure_logging

        install_sdk_failure_logging()
