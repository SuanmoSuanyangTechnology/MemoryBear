import time
from typing import Any

from src.constants.error_codes import BizCode


def _envelope(code: int, error_code: str, msg: str, error: str, data: Any | None) -> dict:
    return {
        "code": code,
        "error_code": error_code,
        "msg": msg,
        "data": data if data is not None else {},
        "error": error,
        "time": int(time.time() * 1000),
    }


def success(data: Any | None = None, msg: str = "OK") -> dict:
    """成功响应（A 类：HTTP 2xx + ``code=0``）。

    ``error_code`` 恒为空串——保持字段恒存在，客户端可无条件读取。
    """
    return _envelope(BizCode.OK, "", msg, "", data)


def fail(code: BizCode, msg: str, error: str = "", data: Any | None = None) -> dict:
    """业务性失败响应（B 类：HTTP 200 + ``code≠0``）。

    用于"请求本身没错、业务上无结果"的场景（如该用户没有可视化数据）。
    "请求有误"请用 ``src/i18n/exceptions`` 的异常类（C 类，真 HTTP 状态）。

    Args:
        code: **只接受 ``BizCode``**——传裸 int（如 HTTP 状态码 400）会抛
            ``ValueError``，防止把 HTTP 状态当业务码用。响应里的 ``error_code``
            由它取名（``code.name``），与异常路径同源。
        msg: 已翻译的提示文案（调用方用 ``Depends(get_translator)`` 取）。
        error: 错误详情；缺省用 msg。
        data: 附加数据。

    Raises:
        TypeError: ``code`` 不是 ``BizCode``（例如误传 HTTP 状态码 400）。
        ValueError: 传了 ``OK``——成功响应请用 ``success()``。
    """
    if not isinstance(code, BizCode):
        raise TypeError(
            f"fail() 只接受 BizCode，收到 {code!r}。"
            f"注意 HTTP 状态码不是业务码——需要真 HTTP 状态请抛 "
            f"src/i18n/exceptions 里的异常；业务码清单见 docs/memory_profile_biz_codes.md"
        )
    if code is BizCode.OK:
        raise ValueError("fail() 不接受 OK；成功响应请用 success()")

    return _envelope(int(code), code.name, msg, error or msg, data)