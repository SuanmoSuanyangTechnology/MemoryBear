"""Load JSON translation files into flattened dot-keys."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

Translations = dict[str, dict[str, str]]


def _flatten(namespace: str, payload: dict[str, Any], target: dict[str, str]) -> None:
    for key, value in payload.items():
        dotted = f"{namespace}.{key}"
        if isinstance(value, dict):
            _flatten(dotted, value, target)
        elif isinstance(value, str):
            target[dotted] = value
        else:
            raise TypeError(f"translation value must be a string: key={dotted}")


def load_translations(locales_dir: Path) -> Translations:
    """Load every ``<locale>/<namespace>.json`` file under ``locales_dir``."""

    if not locales_dir.is_dir():
        raise FileNotFoundError(f"locales directory not found: {locales_dir}")

    translations: Translations = {}
    for locale_dir in sorted(path for path in locales_dir.iterdir() if path.is_dir()):
        messages: dict[str, str] = {}
        for file_path in sorted(locale_dir.glob("*.json")):
            try:
                payload = json.loads(file_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid translation JSON: {file_path}") from exc
            if not isinstance(payload, dict):
                raise TypeError(f"translation file must contain an object: {file_path}")
            _flatten(file_path.stem, payload, messages)
        translations[locale_dir.name] = messages

    if not translations:
        raise RuntimeError(f"no locale directories under {locales_dir}")
    return translations
