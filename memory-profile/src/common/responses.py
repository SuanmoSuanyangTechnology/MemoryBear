"""Generic API response envelope compatible with the monolith contract."""

from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, Field


class ApiResponse[DataT](BaseModel):
    code: int = 0
    msg: str = "OK"
    data: DataT
    error: str = ""
    time: int = Field(default_factory=lambda: int(time.time() * 1000))


def failure(
    code: int,
    msg: str,
    error: str = "",
    data: Any = None,
) -> ApiResponse[Any]:
    """Build an error model for explicit JSONResponse boundaries."""

    return ApiResponse[Any](
        code=code,
        msg=msg,
        data={} if data is None else data,
        error=error,
    )
