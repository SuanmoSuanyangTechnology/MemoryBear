"""Per-package translation catalogs sharing one request-locale context.

Each service keeps its own ``locales/<lang>.json`` resources and default
language; the loading, validation, fallback, and formatting rules live here so
every service renders keys identically. The ``codes`` namespace is injected
from the built-in :mod:`error_core.messages` catalogs, so service files never
duplicate ``BizCode`` copy.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from contextvars import ContextVar
from importlib.resources import files
from pathlib import Path
from types import MappingProxyType

from .i18n import SUPPORTED_LOCALES, normalize_locale
from .i18n import resolve_locale as _resolve_locale
from .messages import code_catalog

logger = logging.getLogger(__name__)

_current_locale: ContextVar[str | None] = ContextVar(
    "error_core_current_locale", default=None
)


def get_current_locale() -> str | None:
    """The locale resolved for the current request, if any."""

    return _current_locale.get()


def set_current_locale(locale: str | None) -> None:
    _current_locale.set(locale)


def _validate_entries(entries: Mapping[str, object]) -> None:
    if not entries:
        raise ValueError("Translation namespaces must not be empty")
    for key, value in entries.items():
        if not isinstance(key, str):
            raise TypeError("Translation keys must be strings")
        if isinstance(value, dict):
            _validate_entries(value)
        elif not isinstance(value, str):
            raise TypeError("Translation values must be strings")


class TranslationCatalog:
    """Load, validate, and render one package's translation resources."""

    def __init__(self, package: str, *, label: str | None = None) -> None:
        self._package = package
        self._label = label or package
        self._default_locale = "zh"
        self._catalogs: Mapping[str, Mapping[str, object]] | None = None

    @property
    def default_locale(self) -> str:
        return self._default_locale

    def configure(self, default_locale: str | None) -> None:
        """Override the fallback locale from validated settings."""

        normalized = normalize_locale(default_locale)
        if normalized in SUPPORTED_LOCALES:
            self._default_locale = normalized
        elif default_locale:
            logger.warning(
                "Unsupported default locale %r; keeping %s",
                default_locale,
                self._default_locale,
            )

    def resolve_locale(
        self,
        explicit: str | None = None,
        language_type: str | None = None,
        accepted: str | None = None,
        preference: str | None = None,
    ) -> str:
        """query ``lang`` > ``X-Language-Type`` > ``Accept-Language`` > preference > default."""

        return _resolve_locale(
            explicit,
            language_type,
            accepted,
            preference,
            default=self._default_locale,
        )

    def load_catalogs(
        self, directory: Path | None = None
    ) -> Mapping[str, Mapping[str, object]]:
        if directory is not None:
            return self._read_catalogs(directory)
        if self._catalogs is None:
            self._catalogs = self._read_catalogs(None)
        return self._catalogs

    def translate(self, key: str, locale: str | None = None, **params: object) -> str:
        """Render one catalog key; unknown keys return themselves (host behavior)."""

        language = normalize_locale(locale) or self._default_locale
        catalogs = self.load_catalogs()
        catalog = catalogs.get(language, catalogs[self._default_locale])
        translation = self._lookup(catalog, key)
        if translation is None and language != self._default_locale:
            translation = self._lookup(catalogs[self._default_locale], key)
        if translation is None:
            logger.warning("Missing translation: %s (locale: %s)", key, language)
            return key
        if params:
            try:
                return translation.format(**params)
            except (KeyError, ValueError, IndexError, TypeError) as exc:
                logger.error("Error formatting translation '%s': %s", key, exc)
        return translation

    def _read_catalogs(self, directory: Path | None) -> Mapping[str, Mapping[str, object]]:
        """Load and validate translation resources at startup."""

        source = directory if directory is not None else files(self._package).joinpath("locales")
        catalogs: dict[str, Mapping[str, object]] = {}
        try:
            for locale in sorted(SUPPORTED_LOCALES):
                raw = json.loads(
                    source.joinpath(f"{locale}.json").read_text(encoding="utf-8")
                )
                if not isinstance(raw, dict) or not raw:
                    raise ValueError("Translation catalog must be a non-empty object")
                if "codes" in raw:
                    raise ValueError("translation namespace conflict: codes")
                merged = dict(raw)
                merged["codes"] = dict(code_catalog(locale))
                _validate_entries(merged)
                catalogs[locale] = MappingProxyType(merged)
        except (OSError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid {self._label} translation resources") from exc
        return MappingProxyType(catalogs)

    @staticmethod
    def _lookup(catalog: Mapping[str, object], key: str) -> str | None:
        parts = key.split(".")
        if len(parts) < 2:
            return None
        current: object = catalog
        for part in parts:
            if not isinstance(current, Mapping) or part not in current:
                return None
            current = current[part]
        return current if isinstance(current, str) else None


__all__ = [
    "SUPPORTED_LOCALES",
    "TranslationCatalog",
    "get_current_locale",
    "set_current_locale",
]
