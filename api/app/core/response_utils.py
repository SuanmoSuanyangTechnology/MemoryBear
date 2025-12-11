import time
from typing import Any, Optional


def success(data: Optional[Any] = None, msg: str = "OK") -> dict:
    return {
        "code": 0,
        "msg": msg,
        "data": data if data is not None else {},
        "error": "",
        "time": int(time.time() * 1000),
    }


def fail(code: int, msg: str, error: str = "", data: Optional[Any] = None) -> dict:
    return {
        "code": code,
        "msg": msg,
        "data": data if data is not None else {},
        "error": error,
        "time": int(time.time() * 1000),
    }