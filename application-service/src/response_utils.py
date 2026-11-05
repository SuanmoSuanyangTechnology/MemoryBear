import time
from typing import Any, Optional

from error_core.codes import BizCode
from error_core.messages import user_message

from .i18n import get_current_locale


def success(data: Optional[Any] = None, msg: Optional[str] = None) -> dict:
    if msg is None:
        msg = user_message(BizCode.OK, get_current_locale()) or "成功"
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
