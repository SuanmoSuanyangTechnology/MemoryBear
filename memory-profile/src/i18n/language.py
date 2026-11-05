"""Request language negotiation."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from src.i18n.core import _supported_set, default_language, t

LANGUAGE_HEADER = "x-language-type"
ACCEPT_LANGUAGE_HEADER = "accept-language"


def normalize_language(value: str | None) -> str | None:
    """Return a supported primary language code, or ``None``."""

    if not value:
        return None
    # Fast path: the middleware and the API proxy normally send a bare "zh"/"en".
    if value in _supported_set:
        return value
    # "en-US,en;q=0.9" -> "en"
    first = value.split(",", 1)[0].split(";", 1)[0].strip().lower()
    primary = first.split("-", 1)[0]
    return primary if primary in _supported_set else None


def resolve_language(
    language_type: str | None,
    accept_language: str | None = None,
) -> str:
    """Pick the request language: explicit header, then Accept-Language, then default."""

    if language_type:
        if language_type in _supported_set:
            return language_type
        resolved = normalize_language(language_type)
        if resolved is not None:
            return resolved
    if accept_language:
        resolved = normalize_language(accept_language)
        if resolved is not None:
            return resolved
    return default_language()


def get_request_language(request: Request) -> str:
    """Read the language set by ``LanguageMiddleware`` (fallback for direct calls)."""

    language = getattr(request.state, "language", None)
    if isinstance(language, str) and language in _supported_set:
        return language
    return default_language()


def get_header_language(request: Request) -> str:
    """The controller-level language rule: only ``X-Language-Type``, default ``zh``.

    The monolith's ``get_language_from_header`` ignores ``Accept-Language``;
    controller messages that were copied from it follow this rule, while
    ``HTTPException``/validation messages use the middleware negotiation.
    Reuses :func:`resolve_language` without ``Accept-Language``; the API proxy
    sends a bare ``zh``/``en``, so the region-tag normalization it applies (e.g.
    ``en-US`` -> ``en``) never differs from the monolith's exact match in
    practice.
    """

    return resolve_language(request.headers.get(LANGUAGE_HEADER))


def translate_request(request: Request, key: str, **params: Any) -> str:
    """Translate a controller-level message in the caller's header language."""

    return t(key, get_header_language(request), **params)
