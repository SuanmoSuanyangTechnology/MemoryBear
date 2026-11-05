"""Locale parsing shared by HTTP middleware and gRPC servicers."""

from __future__ import annotations

import math
import re

SUPPORTED_LOCALES = frozenset({"zh", "en"})
_LANGUAGE_TAG = re.compile(r"^(zh|en)(?:-[A-Za-z0-9]{1,8})*$", re.IGNORECASE)


def normalize_locale(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    match = _LANGUAGE_TAG.fullmatch(value.strip())
    return match.group(1).lower() if match else None


def parse_accept_language(header: str | None) -> str | None:
    if not header:
        return None
    candidates: list[tuple[float, int, str]] = []
    for index, item in enumerate(header.split(",")):
        parts = item.strip().split(";")
        language = normalize_locale(parts[0].split("-")[0])
        if not language:
            continue
        quality = 1.0
        for parameter in parts[1:]:
            match = re.search(r"(?i)\bq\s*=\s*([\d.]+)", parameter)
            if not match:
                continue
            try:
                quality = float(match.group(1))
            except ValueError:
                quality = 1.0
            break
        quality = min(max(quality, 0.0), 1.0)
        if quality > 0.0 and math.isfinite(quality):
            candidates.append((quality, -index, language))
    if not candidates:
        return None
    return max(candidates)[2]


def resolve_locale(
    explicit: str | None = None,
    language_type: str | None = None,
    accepted: str | None = None,
    preference: str | None = None,
    default: str = "zh",
) -> str:
    """query ``lang`` > ``X-Language-Type`` > ``Accept-Language`` > preference > default."""

    for candidate in (explicit, language_type):
        selected = normalize_locale(candidate)
        if selected:
            return selected
    selected = parse_accept_language(accepted)
    if selected:
        return selected
    return normalize_locale(preference) or normalize_locale(default) or "zh"


__all__ = [
    "SUPPORTED_LOCALES",
    "normalize_locale",
    "parse_accept_language",
    "resolve_locale",
]
