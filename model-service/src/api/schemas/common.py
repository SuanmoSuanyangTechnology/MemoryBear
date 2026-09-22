"""Legacy-compatible response envelope helpers."""

from __future__ import annotations

import time
from typing import Any


def success(data: Any = None, msg: str = "OK") -> dict[str, Any]:
    """Return the exact legacy success envelope."""

    return {
        "code": 0,
        "msg": msg,
        "data": data if data is not None else {},
        "error": "",
        "time": int(time.time() * 1000),
    }


def fail(
    code: int,
    msg: str,
    error: Any = "",
    data: Any = None,
) -> dict[str, Any]:
    """Return the exact legacy failure envelope."""

    return {
        "code": code,
        "msg": msg,
        "data": data if data is not None else {},
        "error": error,
        "time": int(time.time() * 1000),
    }


__all__ = ["fail", "success"]
