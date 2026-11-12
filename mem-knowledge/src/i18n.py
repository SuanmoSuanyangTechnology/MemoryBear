"""Validated, immutable service translations and request language selection."""

from __future__ import annotations

import json
import logging
import math
import re
from collections.abc import Mapping
from functools import lru_cache
from importlib.resources import files
from types import MappingProxyType

from error_core.knowledge import knowledge_message

from .errors import ERROR_DEFINITIONS, PublicScalar

logger = logging.getLogger(__name__)
SUPPORTED_LOCALES = frozenset({"zh", "en"})
_LANGUAGE_TAG = re.compile(r"^(zh|en)(?:-[a-z0-9]{1,8})*$", re.IGNORECASE)


def normalize_locale(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    match = _LANGUAGE_TAG.fullmatch(value.strip())
    return match.group(1).lower() if match else None


def resolve_locale(
    explicit: str | None = None,
    accepted: str | None = None,
    preference: str | None = None,
) -> str:
    """Select explicit language, weighted header, existing preference, then Chinese."""
    selected = normalize_locale(explicit)
    if selected:
        return selected
    candidates: list[tuple[float, int, str]] = []
    for index, item in enumerate((accepted or "").split(",")):
        parts = item.strip().split(";")
        language = normalize_locale(parts[0])
        if not language:
            continue
        weight = 1.0
        if len(parts) > 1:
            if len(parts) != 2 or not parts[1].strip().lower().startswith("q="):
                continue
            try:
                weight = float(parts[1].strip()[2:])
            except ValueError:
                continue
        if math.isfinite(weight) and 0 < weight <= 1:
            candidates.append((weight, -index, language))
    if candidates:
        return max(candidates)[2]
    return normalize_locale(preference) or "zh"


@lru_cache(maxsize=1)
def load_catalogs() -> Mapping[str, Mapping[str, str]]:
    """Resolve static messages from the shared catalog at startup."""
    return MappingProxyType(
        {
            locale: MappingProxyType(
                {
                    key: knowledge_message(definition.response_code, locale)
                    for key, definition in ERROR_DEFINITIONS.items()
                }
            )
            for locale in SUPPORTED_LOCALES
        }
    )


def translate(
    key: str,
    locale: str,
    params: Mapping[str, PublicScalar] | None = None,
) -> str:
    # 旧参数仅保留给诊断；本期用户提示不插值。
    definition = ERROR_DEFINITIONS.get(key, ERROR_DEFINITIONS["KB_INTERNAL_ERROR"])
    return knowledge_message(definition.response_code, normalize_locale(locale) or "zh")


@lru_cache(maxsize=1)
def legacy_catalogs() -> Mapping[str, Mapping[str, str]]:
    """Existing task progress and successful business refusals keep their message contract."""
    return MappingProxyType(
        {
            locale: MappingProxyType(
                json.loads(
                    files(__package__)
                    .joinpath("locales", f"{locale}.json")
                    .read_text(encoding="utf-8")
                )
            )
            for locale in SUPPORTED_LOCALES
        }
    )


def translate_legacy(key: str, locale: str, params: Mapping | None = None) -> str:
    catalog = legacy_catalogs()[normalize_locale(locale) or "zh"]
    template = catalog.get(key, catalog["KB_INTERNAL_ERROR"])
    try:
        return template.format_map(params or {})
    except (KeyError, ValueError, TypeError):
        return catalog["KB_INTERNAL_ERROR"]
