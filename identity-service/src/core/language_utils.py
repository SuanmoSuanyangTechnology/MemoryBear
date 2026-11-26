"""Language header helpers shared by the migrated management routes.

Host semantics: case-insensitive match against the supported set, unknown or
missing values fall back to the default language.
"""

from __future__ import annotations

import logging

from src.config import settings

logger = logging.getLogger(__name__)


def validate_language(language: str | None) -> str:
    if language is None:
        return settings.I18N_DEFAULT_LANGUAGE

    # 处理枚举类型：优先取 .value，避免 str(Language.ZH) → "Language.ZH"
    if hasattr(language, "value"):
        language = language.value

    lang = str(language).lower().strip()
    if lang in settings.I18N_SUPPORTED_LANGUAGES:
        return lang

    logger.warning(
        "无效的语言参数 '%s'，已回退到默认值 '%s'。支持的语言: %s",
        language,
        settings.I18N_DEFAULT_LANGUAGE,
        settings.I18N_SUPPORTED_LANGUAGES,
    )
    return settings.I18N_DEFAULT_LANGUAGE


def get_language_from_header(language_type: str | None) -> str:
    return validate_language(language_type)
