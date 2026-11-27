"""Locale selection and translations for the management surface.

The catalog machinery is shared through ``error_core``; this module binds it to
this service's ``src/locales`` resources and configurable default language.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from error_core.i18n import SUPPORTED_LOCALES, normalize_locale
from error_core.middleware import LanguageContextMiddleware as _LanguageContextMiddleware
from error_core.translations import (
    TranslationCatalog,
    get_current_locale,
    set_current_locale,
)
from fastapi import Request

_catalog = TranslationCatalog("src", label="identity service")


def configure(default_locale: str | None) -> None:
    _catalog.configure(default_locale)


def get_default_locale() -> str:
    return _catalog.default_locale


def resolve_locale(
    explicit: str | None = None,
    language_type: str | None = None,
    accepted: str | None = None,
    preference: str | None = None,
) -> str:
    """query ``lang`` > ``X-Language-Type`` > ``Accept-Language`` > preference > default."""

    return _catalog.resolve_locale(explicit, language_type, accepted, preference)


def load_catalogs(directory: Path | None = None):
    return _catalog.load_catalogs(directory)


def translate(key: str, locale: str | None = None, **params: object) -> str:
    """Render one catalog key; unknown keys return themselves (host behavior)."""

    return _catalog.translate(key, locale, **params)


def translate_enum(enum_type: str, value: str, locale: str | None = None) -> str:
    """Host ``translate_enum``: ``enums.<type>.<value>`` in the catalog."""

    return translate(f"enums.{enum_type}.{value}", locale)


class LanguageContextMiddleware(_LanguageContextMiddleware):
    """Resolve the request language and echo it on the response."""

    default_locale = staticmethod(get_default_locale)


async def get_translator(request: Request) -> Callable[..., str]:
    """Request-bound ``t(key, **params)`` (same dependency name as the host)."""

    language = getattr(request.state, "language", None) or get_default_locale()

    def t(key: str, **params: object) -> str:
        return translate(key, language, **params)

    return t


async def get_enum_translator(request: Request) -> Callable[[str, str], str]:
    """Request-bound ``t_enum(enum_type, value)`` (same dependency name as the host)."""

    language = getattr(request.state, "language", None) or get_default_locale()

    def t_enum(enum_type: str, value: str) -> str:
        return translate_enum(enum_type, value, language)

    return t_enum


__all__ = [
    "LanguageContextMiddleware",
    "SUPPORTED_LOCALES",
    "configure",
    "get_current_locale",
    "get_default_locale",
    "get_enum_translator",
    "get_translator",
    "load_catalogs",
    "normalize_locale",
    "resolve_locale",
    "set_current_locale",
    "translate",
    "translate_enum",
]
