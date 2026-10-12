"""Locale selection and legacy-compatible translations for model service errors.

Wire-compatible subset of the host i18n service: only the namespaces the model
domain can render (``errors.common.*`` HTTP mapping, quota resources) are
cataloged. Key resolution, default-locale fallback, and formatting tolerance
match the host so responses stay byte-identical for the same request language.
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections.abc import Mapping
from contextvars import ContextVar
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from types import MappingProxyType

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)

SUPPORTED_LOCALES = frozenset({"zh", "en"})
_LANGUAGE_TAG = re.compile(r"^(zh|en)(?:-[A-Za-z0-9]{1,8})*$")
_default_locale = "zh"

_current_locale: ContextVar[str | None] = ContextVar("model_current_locale", default=None)


def configure(default_locale: str | None) -> None:
    """Override the fallback locale from validated settings."""

    global _default_locale
    normalized = normalize_locale(default_locale)
    if normalized:
        _default_locale = normalized


def get_default_locale() -> str:
    return _default_locale


def get_current_locale() -> str | None:
    return _current_locale.get()


def set_current_locale(locale: str | None) -> None:
    _current_locale.set(locale)


def normalize_locale(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    match = _LANGUAGE_TAG.fullmatch(value.strip())
    return match.group(1).lower() if match else None


def _parse_accept_language(header: str) -> str | None:
    """Mirror the host parser: base tag, quality sorted, first supported wins."""

    candidates: list[tuple[float, int, str]] = []
    for index, item in enumerate(header.split(",")):
        parts = item.strip().split(";")
        language = normalize_locale(parts[0].split("-")[0])
        if not language:
            continue
        quality = 1.0
        if len(parts) > 1:
            match = re.search(r"q=([\d.]+)", parts[1])
            if match:
                try:
                    quality = float(match.group(1))
                except ValueError:
                    quality = 1.0
        if math.isfinite(quality):
            candidates.append((quality, -index, language))
    if not candidates:
        return None
    return max(candidates)[2]


def resolve_locale(
    explicit: str | None = None,
    accepted: str | None = None,
    preference: str | None = None,
) -> str:
    """Select query language, weighted header, caller preference, then default."""

    selected = normalize_locale(explicit)
    if selected:
        return selected
    if accepted:
        selected = _parse_accept_language(accepted)
        if selected:
            return selected
    return normalize_locale(preference) or _default_locale


def _validate_entries(entries: Mapping[str, object]) -> None:
    if not entries:
        raise ValueError("Translation namespaces must not be empty")
    for key, value in entries.items():
        if not isinstance(key, str):
            raise ValueError("Translation keys must be strings")
        if isinstance(value, dict):
            _validate_entries(value)
        elif not isinstance(value, str):
            raise ValueError("Translation values must be strings")


def _read_catalogs(directory: Path | None) -> Mapping[str, Mapping[str, object]]:
    """Load and validate translation resources at startup."""

    source = directory if directory is not None else files(__package__).joinpath("locales")
    catalogs: dict[str, Mapping[str, object]] = {}
    try:
        for locale in sorted(SUPPORTED_LOCALES):
            raw = json.loads(
                source.joinpath(f"{locale}.json").read_text(encoding="utf-8")
            )
            if not isinstance(raw, dict) or not raw:
                raise ValueError("Translation catalog must be a non-empty object")
            _validate_entries(raw)
            catalogs[locale] = MappingProxyType(raw)
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError("Invalid model service translation resources") from exc
    return MappingProxyType(catalogs)


@lru_cache(maxsize=1)
def _package_catalogs() -> Mapping[str, Mapping[str, object]]:
    return _read_catalogs(None)


def load_catalogs(directory: Path | None = None) -> Mapping[str, Mapping[str, object]]:
    return _package_catalogs() if directory is None else _read_catalogs(directory)


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


def translate(key: str, locale: str | None = None, **params: object) -> str:
    """Render one catalog key; unknown keys return themselves (host behavior)."""

    language = normalize_locale(locale) or _default_locale
    catalogs = load_catalogs()
    catalog = catalogs.get(language, catalogs[_default_locale])
    translation = _lookup(catalog, key)
    if translation is None and language != _default_locale:
        translation = _lookup(catalogs[_default_locale], key)
    if translation is None:
        logger.warning("Missing translation: %s (locale: %s)", key, language)
        return key
    if params:
        try:
            return translation.format(**params)
        except (KeyError, ValueError, IndexError, TypeError) as exc:
            logger.error("Error formatting translation '%s': %s", key, exc)
    return translation


class LanguageContextMiddleware(BaseHTTPMiddleware):
    """Resolve the request language and echo it on the response."""

    async def dispatch(self, request: Request, call_next) -> Response:
        language = resolve_locale(
            explicit=request.query_params.get("lang"),
            accepted=request.headers.get("Accept-Language"),
        )
        request.state.language = language
        set_current_locale(language)
        response = await call_next(request)
        response.headers["Content-Language"] = language
        return response


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
