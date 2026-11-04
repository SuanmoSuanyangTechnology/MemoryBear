"""Legacy-compatible envelopes and the unified SSE error frame."""

from __future__ import annotations

import time
from typing import Any


def now_ms() -> int:
    return int(time.time() * 1000)


def success(data: Any = None, msg: str = "OK") -> dict[str, Any]:
    return {
        "code": 0,
        "msg": msg,
        "data": data if data is not None else {},
        "error": "",
        "time": now_ms(),
    }


def fail(code: int, msg: str, error: str = "", data: Any = None) -> dict[str, Any]:
    return {
        "code": code,
        "msg": msg,
        "data": data if data is not None else {},
        "error": error,
        "time": now_ms(),
    }


def error_frame(
    *,
    code: int,
    msg: str,
    error: str,
    retryable: bool,
    attempts: int = 0,
    channel_id: str | None = None,
    retry_after_seconds: int | None = None,
) -> dict[str, Any]:
    """SSE ``error`` payload; ``message`` is the deprecated alias of ``error``."""

    frame: dict[str, Any] = {
        "code": code,
        "msg": msg,
        "error": error,
        "message": error,
        "attempts": attempts,
        "channel_id": channel_id,
        "retryable": retryable,
        "time": now_ms(),
    }
    if retry_after_seconds is not None:
        frame["retry_after_seconds"] = retry_after_seconds
    return frame


__all__ = ["error_frame", "fail", "now_ms", "success"]
