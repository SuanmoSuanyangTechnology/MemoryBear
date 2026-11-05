"""Legacy-compatible response envelope helpers."""

from __future__ import annotations

from typing import Any

from error_core.envelope import fail
from error_core.envelope import success as _success

from ...i18n import get_current_locale


def success(data: Any = None, msg: str | None = None) -> dict[str, Any]:
    """Return the exact legacy success envelope."""

    return _success(data, msg=msg, locale=get_current_locale())


__all__ = ["fail", "success"]
