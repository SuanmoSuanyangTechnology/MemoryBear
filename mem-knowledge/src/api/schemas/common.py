"""Legacy-compatible HTTP response contracts for migrated business APIs."""

from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, Field


class ApiResponse[DataT](BaseModel):
    """Wire-compatible copy of the legacy API response envelope."""

    code: int = 0
    msg: str = "OK"
    data: DataT | dict[str, Any] = Field(default_factory=dict)
    error: str = ""
    time: int = Field(default_factory=lambda: int(time.time() * 1000))


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


# Keep the internal name as a source-compatibility alias while routes migrate.
SuccessEnvelope = ApiResponse


__all__ = ["ApiResponse", "SuccessEnvelope", "fail", "success"]
