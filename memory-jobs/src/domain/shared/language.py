"""Supported output languages.

``Language.parse`` is the domain's single interpretation of a client language
tag. It keeps the behaviour the service has always had: BCP-47 and the
POSIX-style ``en_US`` form both resolve to their primary subtag, and anything
unsupported falls back to Chinese.
"""

from __future__ import annotations

from enum import StrEnum


class Language(StrEnum):
    ZH = "zh"
    EN = "en"

    @classmethod
    def parse(cls, value: str | None) -> Language:
        """Return the supported primary language; default to Chinese."""

        if not value:
            return cls.ZH
        first = value.split(",", 1)[0].split(";", 1)[0].strip().lower()
        # Accept both BCP-47 (``en-US``) and the POSIX/misconfigured form (``en_US``).
        primary = first.replace("_", "-").split("-", 1)[0]
        return cls.EN if primary == cls.EN.value else cls.ZH
