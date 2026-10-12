"""Sensitive-data masking for messages and contexts before they reach a client.

Wire-compatible port of the host ``app/core/sensitive_filter.py``: business
messages rendered into the legacy envelope are masked with the same patterns
and the same ``ENABLE_SENSITIVE_DATA_FILTER`` gate.
"""

from __future__ import annotations

import re
from typing import Any

from .config import ModelServiceSettings

REDACTED_TEXT = "***REDACTED***"

SENSITIVE_KEYS = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "token",
        "access_token",
        "refresh_token",
        "token_id",
        "secret",
        "api_key",
        "apikey",
        "authorization",
        "auth",
        "private_key",
        "secret_key",
        "session_id",
        "sessionid",
        "csrf_token",
        "credit_card",
        "card_number",
        "cvv",
        "ssn",
    }
)

SENSITIVE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"), "[EMAIL]"),
    (re.compile(r"\b1[3-9]\d{9}\b"), "[PHONE]"),
    (re.compile(r"\b\d{15,19}\b"), "[CARD]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"), "[TOKEN]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]*)?"), "[TOKEN]"),
    (
        re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I),
        "[UUID]",
    ),
    (re.compile(r"\b[A-Za-z0-9]{32,}\b"), "[API_KEY]"),
)


def _is_sensitive_key(key: str) -> bool:
    key_lower = key.lower()
    return any(sensitive_key in key_lower for sensitive_key in SENSITIVE_KEYS)


class SensitiveDataFilter:
    """Filter credentials out of messages and contexts with a shared gate."""

    _enabled: bool | None = None

    @classmethod
    def configure(cls, settings: ModelServiceSettings) -> None:
        cls._enabled = settings.enable_sensitive_data_filter

    @classmethod
    def is_enabled(cls) -> bool:
        return bool(cls._enabled)

    @classmethod
    def filter_string(cls, text: str) -> str:
        if not cls.is_enabled() or not isinstance(text, str):
            return text
        filtered = text
        for pattern, replacement in SENSITIVE_PATTERNS:
            filtered = pattern.sub(replacement, filtered)
        return filtered

    @classmethod
    def filter_dict(cls, data: dict[str, Any], deep: bool = True) -> dict[str, Any]:
        if not cls.is_enabled() or not isinstance(data, dict):
            return data
        filtered: dict[str, Any] = {}
        for key, value in data.items():
            if _is_sensitive_key(key):
                filtered[key] = REDACTED_TEXT
            elif isinstance(value, dict) and deep:
                filtered[key] = cls.filter_dict(value, deep=True)
            elif isinstance(value, list) and deep:
                filtered[key] = [
                    cls.filter_dict(item, deep=True) if isinstance(item, dict) else item
                    for item in value
                ]
            elif isinstance(value, str):
                filtered[key] = cls.filter_string(value)
            else:
                filtered[key] = value
        return filtered

    @classmethod
    def filter_message(
        cls, message: str, context: dict[str, Any] | None = None
    ) -> tuple[str, dict[str, Any]]:
        filtered_message = cls.filter_string(message)
        filtered_context = cls.filter_dict(context) if context else {}
        return filtered_message, filtered_context
