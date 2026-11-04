"""Built-in zh/en catalogs keyed by ``BizCode`` member name."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from functools import lru_cache
from importlib.resources import files
from types import MappingProxyType

from .codes import BizCode, code_name, coerce_code

logger = logging.getLogger(__name__)

SUPPORTED_LOCALES = ("zh", "en")
_DEFAULT_LOCALE = "zh"


def _read_locale(locale: str) -> Mapping[str, str]:
    raw = json.loads(
        files(__package__).joinpath("locales", f"{locale}.json").read_text(encoding="utf-8")
    )
    codes = raw.get("codes") if isinstance(raw, dict) else None
    if not isinstance(codes, dict) or not codes:
        raise ValueError(f"error-core catalog {locale} is missing the 'codes' object")
    for key, value in codes.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError(f"error-core catalog {locale} has a non-string entry")
    return MappingProxyType(dict(codes))


@lru_cache(maxsize=1)
def load_catalogs() -> Mapping[str, Mapping[str, str]]:
    return MappingProxyType({locale: _read_locale(locale) for locale in SUPPORTED_LOCALES})


def code_catalog(locale: str | None = None) -> Mapping[str, str]:
    catalogs = load_catalogs()
    selected = locale if locale in catalogs else _DEFAULT_LOCALE
    return catalogs[selected]


def user_message(
    code: BizCode | int | str | None,
    locale: str | None = None,
    **params: object,
) -> str | None:
    """Render ``codes.<NAME>``; unknown codes or bad params return ``None``."""

    numeric = coerce_code(code)
    name = code_name(numeric)
    if numeric is None or not _is_known(numeric):
        logger.warning("unlocalized_error_code code=%s locale=%s", code, locale)
        return None
    catalogs = load_catalogs()
    selected = locale if locale in catalogs else _DEFAULT_LOCALE
    template = catalogs[selected].get(name) or catalogs[_DEFAULT_LOCALE].get(name)
    if template is None:
        logger.warning("unlocalized_error_code code=%s locale=%s", name, locale)
        return None
    if not params:
        return template
    try:
        return template.format(**params)
    except (KeyError, IndexError, ValueError, TypeError):
        logger.warning("unlocalized_error_code code=%s locale=%s reason=params", name, locale)
        return None


def fallback_message(http_status: int, locale: str | None = None) -> str:
    selected = locale if locale in load_catalogs() else _DEFAULT_LOCALE
    if http_status >= 500:
        return (
            "Service is temporarily unavailable, please try again later"
            if selected == "en"
            else "服务暂时不可用，请稍后重试"
        )
    return "请求处理失败，请检查后重试" if selected == "zh" else "Request failed, please check and try again"


def _is_known(numeric: int) -> bool:
    return numeric in BizCode._value2member_map_


__all__ = [
    "SUPPORTED_LOCALES",
    "code_catalog",
    "fallback_message",
    "load_catalogs",
    "user_message",
]
