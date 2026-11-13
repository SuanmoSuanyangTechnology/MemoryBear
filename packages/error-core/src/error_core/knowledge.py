"""Static knowledge messages shared by the service and its direct callers."""

from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files

from .codes import code_name, is_known_code
from .diagnostics import redact, truncate_utf8
from .messages import user_message


@lru_cache(maxsize=2)
def _messages(locale: str) -> dict[str, str]:
    catalog = json.loads(
        files(__package__).joinpath("locales", f"{locale}.json").read_text()
    )
    return catalog.get("knowledge_codes", {})


def knowledge_message(
    code: int, locale: str = "zh", *, legacy_message: str | None = None
) -> str:
    if not is_known_code(code) and legacy_message:
        return legacy_message
    language = "en" if locale.lower().split("-")[0] == "en" else "zh"
    return (
        _messages(language).get(code_name(code))
        or user_message(code, language)
        or _messages(language)["INTERNAL_ERROR"]
    )


def validation_diagnostic(errors: list[dict]) -> str:
    """Expose real field locations/reasons without Pydantic input or context values."""
    parts = []
    for error in errors:
        location = ".".join(str(part) for part in error.get("loc", ()))
        sensitive = any(
            marker in location.lower()
            for marker in (
                "password",
                "secret",
                "token",
                "authorization",
                "api_key",
                "api-key",
                "cookie",
            )
        )
        reason = "Invalid sensitive field" if sensitive else error["msg"]
        parts.append(f"{location}: {reason} ({error['type']})")
    return truncate_utf8(redact("; ".join(parts)), 8192)
