"""Locale selection and translations for application service errors.

The catalog machinery is shared through ``error_core``; this module binds it to
this service's ``src/locales`` resources and configurable default language.
"""

from __future__ import annotations

from pathlib import Path

from error_core.i18n import SUPPORTED_LOCALES, normalize_locale
from error_core.middleware import LanguageContextMiddleware as _LanguageContextMiddleware
from error_core.translations import (
    TranslationCatalog,
    get_current_locale,
    set_current_locale,
)

_catalog = TranslationCatalog("src", label="application service")


def configure(default_locale: str | None) -> None:
    """Override the fallback locale from validated settings."""

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


class LanguageContextMiddleware(_LanguageContextMiddleware):
    """Resolve the request language and echo it on the response."""

    default_locale = staticmethod(get_default_locale)


__all__ = [
    "LanguageContextMiddleware",
    "SUPPORTED_LOCALES",
    "configure",
    "get_current_locale",
    "get_default_locale",
    "load_catalogs",
    "normalize_locale",
    "resolve_locale",
    "set_current_locale",
    "translate",
]
