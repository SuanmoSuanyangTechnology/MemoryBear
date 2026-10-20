"""Process-wide translation store and lookup."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from src.i18n.loader import Translations, load_translations

logger = logging.getLogger(__name__)

DEFAULT_LANGUAGE = "zh"
FALLBACK_LANGUAGE = "zh"
SUPPORTED_LANGUAGES: tuple[str, ...] = ("zh", "en")
LOCALES_DIR = Path(__file__).resolve().parent.parent / "locales"


class EnumLabels:
    """Pre-built enum display maps for one locale.

    The graph hot path resolves labels once per request and then only performs
    plain ``dict.get`` calls per node (no key building, no locale normalization).
    """

    __slots__ = ("entity_type", "emotion_type", "emotion_subject", "preference_group_name")

    def __init__(
        self,
        entity_type: dict[str, str],
        emotion_type: dict[str, str],
        emotion_subject: dict[str, str],
        preference_group_name: str,
    ) -> None:
        self.entity_type = entity_type
        self.emotion_type = emotion_type
        self.emotion_subject = emotion_subject
        self.preference_group_name = preference_group_name


_EMPTY_LABELS = EnumLabels({}, {}, {}, "")

_translations: Translations = {}
_enum_labels: dict[str, EnumLabels] = {}
_default_messages: dict[str, str] = {}
_fallback_messages: dict[str, str] = {}
_supported_set: set[str] = set(SUPPORTED_LANGUAGES)
_default_language = DEFAULT_LANGUAGE
_fallback_language = FALLBACK_LANGUAGE
_supported_languages: tuple[str, ...] = SUPPORTED_LANGUAGES
_configured = False


def configure_i18n(
    *,
    locales_dir: Path | str | None = None,
    default_language: str | None = None,
    fallback_language: str | None = None,
    supported_languages: list[str] | tuple[str, ...] | None = None,
) -> None:
    """Load every locale into memory once, at application startup."""

    global _translations, _enum_labels, _default_messages, _fallback_messages
    global _default_language, _fallback_language, _supported_languages, _configured

    resolved_dir = Path(locales_dir) if locales_dir is not None else LOCALES_DIR
    translations = load_translations(resolved_dir)

    default = (default_language or DEFAULT_LANGUAGE).strip().lower()
    fallback = (fallback_language or FALLBACK_LANGUAGE).strip().lower()
    supported = tuple(
        language.strip().lower()
        for language in (supported_languages or SUPPORTED_LANGUAGES)
        if language.strip()
    )
    if default not in supported:
        raise ValueError(
            f"default language {default!r} is not in supported languages {supported}"
        )
    if default not in translations:
        raise RuntimeError(f"default locale {default!r} is missing under {resolved_dir}")

    resolved_fallback = fallback if fallback in translations else default
    _translations = translations
    _enum_labels = {
        locale: _build_enum_labels(messages) for locale, messages in translations.items()
    }
    _default_language = default
    _fallback_language = resolved_fallback
    _default_messages = translations[default]
    _fallback_messages = translations[resolved_fallback]
    _supported_languages = supported
    _supported_set.clear()
    _supported_set.update(supported)
    _configured = True
    logger.info(
        "i18n configured default=%s fallback=%s supported=%s locales=%s",
        _default_language,
        _fallback_language,
        ",".join(supported),
        ",".join(sorted(translations)),
    )


def clear_i18n() -> None:
    """Drop loaded translations (tests and process shutdown)."""

    global _translations, _enum_labels, _default_messages, _fallback_messages, _configured
    _translations = {}
    _enum_labels = {}
    _default_messages = {}
    _fallback_messages = {}
    _supported_set.clear()
    _supported_set.update(SUPPORTED_LANGUAGES)
    _configured = False


def default_language() -> str:
    return _default_language


def fallback_language() -> str:
    return _fallback_language


def supported_languages() -> tuple[str, ...]:
    return _supported_languages


def available_locales() -> tuple[str, ...]:
    return tuple(sorted(_translations))


def is_configured() -> bool:
    return _configured


def _collect_prefix(messages: dict[str, str], prefix: str) -> dict[str, str]:
    return {key[len(prefix):]: value for key, value in messages.items() if key.startswith(prefix)}


def _build_enum_labels(messages: dict[str, str]) -> EnumLabels:
    return EnumLabels(
        entity_type=_collect_prefix(messages, "analytics.enum.entity_type."),
        emotion_type=_collect_prefix(messages, "analytics.enum.emotion_type."),
        emotion_subject=_collect_prefix(messages, "analytics.enum.emotion_subject."),
        preference_group_name=messages.get("analytics.preference_group.name", ""),
    )


def enum_labels(locale: str | None = None) -> EnumLabels:
    """Return the pre-built label maps for ``locale`` (default/fallback aware)."""

    if locale is not None:
        labels = _enum_labels.get(locale)
        if labels is not None:
            return labels
    else:
        labels = _enum_labels.get(_default_language)
        if labels is not None:
            return labels
    return _enum_labels.get(_fallback_language, _EMPTY_LABELS)


def _format(key: str, value: str, params: dict[str, Any]) -> str:
    try:
        return value.format(**params)
    except (KeyError, IndexError, ValueError):
        logger.warning(
            "failed to format translation key=%s params=%s",
            key,
            sorted(params),
            exc_info=True,
        )
        return value


def _warn_missing(key: str, language: str) -> None:
    if not _configured:
        logger.warning("i18n is not configured; missing key=%s", key)
    else:
        logger.warning("missing translation key=%s locale=%s", key, language)


def translate(key: str, locale: str | None = None, **params: Any) -> str:
    """Return the translation, or ``key`` itself when it is missing.

    Lookup is inlined on purpose: this function is called on every response
    that carries text, and the extra helper call showed up in profiles.
    """

    if locale is None:
        messages = _default_messages
        language = _default_language
    else:
        messages = _translations.get(locale)
        if messages is None:
            language = locale.strip().lower()
            messages = _translations.get(language, {})
        else:
            language = locale
    value = messages.get(key)
    if value is None and language != _fallback_language:
        value = _fallback_messages.get(key)
    if value is None:
        _warn_missing(key, language)
        return key
    if params:
        return _format(key, value, params)
    return value


def message(key: str, locale: str | None = None) -> str:
    """Parameterless fast path: no ``**kwargs`` dict is allocated per call."""

    if locale is None:
        messages = _default_messages
        language = _default_language
    else:
        messages = _translations.get(locale)
        if messages is None:
            language = locale.strip().lower()
            messages = _translations.get(language, {})
        else:
            language = locale
    value = messages.get(key)
    if value is None and language != _fallback_language:
        value = _fallback_messages.get(key)
    if value is None:
        _warn_missing(key, language)
        return key
    return value


def translate_optional(key: str, locale: str | None = None, **params: Any) -> str | None:
    """Return ``None`` when the key is missing (for optional data fields)."""

    if locale is None:
        messages = _default_messages
        language = _default_language
    else:
        messages = _translations.get(locale)
        if messages is None:
            language = locale.strip().lower()
            messages = _translations.get(language, {})
        else:
            language = locale
    value = messages.get(key)
    if value is None and language != _fallback_language:
        value = _fallback_messages.get(key)
    if value is None:
        return None
    if params:
        return _format(key, value, params)
    return value


def translate_or(key: str, default: str, locale: str | None = None, **params: Any) -> str:
    """Return ``default`` when the key is missing."""

    if locale is None:
        messages = _default_messages
        language = _default_language
    else:
        messages = _translations.get(locale)
        if messages is None:
            language = locale.strip().lower()
            messages = _translations.get(language, {})
        else:
            language = locale
    value = messages.get(key)
    if value is None and language != _fallback_language:
        value = _fallback_messages.get(key)
    if value is None:
        return default
    if params:
        return _format(key, value, params)
    return value


t = translate
t_optional = translate_optional
t_or = translate_or

m = message
