"""Shared error codes, envelopes, diagnostics, and locale helpers."""

from .codes import (
    ERROR_CODE_TO_BIZ_CODE,
    HTTP_MAPPING,
    BizCode,
    as_biz_code,
    code_name,
    coerce_code,
    http_status_for,
    is_known_code,
)
from .envelope import error_frame, fail, now_ms, success
from .i18n import normalize_locale, parse_accept_language, resolve_locale
from .messages import fallback_message, load_catalogs, user_message
from .supplier import (
    AggregatedFailure,
    ChannelFailure,
    ProviderFailure,
    aggregate_channel_failures,
)

__all__ = [
    "ERROR_CODE_TO_BIZ_CODE",
    "HTTP_MAPPING",
    "AggregatedFailure",
    "BizCode",
    "ChannelFailure",
    "ProviderFailure",
    "aggregate_channel_failures",
    "as_biz_code",
    "code_name",
    "coerce_code",
    "error_frame",
    "fail",
    "fallback_message",
    "http_status_for",
    "is_known_code",
    "load_catalogs",
    "normalize_locale",
    "now_ms",
    "parse_accept_language",
    "resolve_locale",
    "success",
    "user_message",
]
