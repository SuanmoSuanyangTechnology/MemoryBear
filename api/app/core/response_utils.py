from typing import Any, Optional

from error_core.envelope import fail, now_ms
from error_core.envelope import success as _success


def success(data: Optional[Any] = None, msg: Optional[str] = None) -> dict:
    from app.i18n.exceptions import get_current_locale

    return _success(data, msg, get_current_locale())


__all__ = ["fail", "now_ms", "success"]
