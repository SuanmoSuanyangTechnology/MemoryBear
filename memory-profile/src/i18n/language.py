"""Request language negotiation."""

from __future__ import annotations

from fastapi import Request

from src.i18n.core import _supported_set, default_language

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
