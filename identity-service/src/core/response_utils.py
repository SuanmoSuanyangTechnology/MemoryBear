from typing import Any

from error_core.envelope import fail, now_ms
from error_core.envelope import success as _success

from src.i18n import get_current_locale


def success(data: Any | None = None, msg: str | None = None) -> dict:
    return _success(data, msg, get_current_locale())


__all__ = ["fail", "now_ms", "success"]
