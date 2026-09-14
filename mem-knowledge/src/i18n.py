"""Validated, immutable service translations and request language selection."""

from __future__ import annotations

import json
import logging
import math
import re
from collections.abc import Mapping
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from string import Formatter
from types import MappingProxyType

from .errors import ERROR_DEFINITIONS, PublicScalar, valid_params

logger = logging.getLogger(__name__)
SUPPORTED_LOCALES = frozenset({"zh", "en"})
_LANGUAGE_TAG = re.compile(r"^(zh|en)(?:-[a-z0-9]{1,8})*$", re.IGNORECASE)
_FORMATTER = Formatter()


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


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate translation key")
        result[key] = value
    return result


@lru_cache(maxsize=1)
def load_catalogs(directory: Path | None = None) -> Mapping[str, Mapping[str, str]]:
    """Load resources at startup; reject corrupt deployments before serving requests."""
    source = directory if directory is not None else files(__package__).joinpath("locales")
    catalogs = {}
    try:
        for locale in sorted(SUPPORTED_LOCALES):
            raw = json.loads(
                source.joinpath(f"{locale}.json").read_text(encoding="utf-8"),
                object_pairs_hook=_unique_object,
            )
            if not isinstance(raw, dict) or raw.keys() != ERROR_DEFINITIONS.keys():
                raise ValueError("Translation keys do not match the error catalog")
            for key, template in raw.items():
                if not isinstance(template, str) or not template.strip():
                    raise ValueError("Translation must be a nonempty string")
                placeholders = set()
                for _, name, format_spec, conversion in _FORMATTER.parse(template):
                    if name is not None:
                        if not name.isidentifier() or format_spec or conversion:
                            raise ValueError("Only named scalar translation parameters are allowed")
                        placeholders.add(name)
                if placeholders != ERROR_DEFINITIONS[key].params.keys():
                    raise ValueError("Translation parameters do not match the error catalog")
            catalogs[locale] = MappingProxyType(raw)
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError("Invalid knowledge translation resources") from exc
    return MappingProxyType(catalogs)


def translate(
    key: str,
    locale: str,
    params: Mapping[str, PublicScalar] | None = None,
) -> str:
    """Render public parameters only; never expose an unknown key or exception text."""
    language = normalize_locale(locale) or "zh"
    catalog = load_catalogs()[language]
    supplied = params if params is not None else {}
    if key not in catalog:
        logger.error("Knowledge translation_missing")
        return catalog["KB_INTERNAL_ERROR"]
    if not valid_params(key, supplied):
        logger.error("Knowledge translation_invalid")
        return catalog["KB_INTERNAL_ERROR"]
    try:
        return catalog[key].format_map(supplied)
    except (KeyError, ValueError, TypeError):
        logger.error("Knowledge translation_invalid")
        return catalog["KB_INTERNAL_ERROR"]
