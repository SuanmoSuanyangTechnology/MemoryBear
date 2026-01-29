import time
from typing import Any, Optional
from fastapi.responses import JSONResponse
from app.core.error_codes import BizCode, HTTP_MAPPING


def success(data: Optional[Any] = None, msg: str = "OK") -> dict:
    """成功响应（HTTP 200）"""
    return {
        "code": 0,
        "msg": msg,
        "data": data if data is not None else {},
        "error": "",
        "time": int(time.time() * 1000),
    }


def fail(code: int, msg: str, error: str = "", data: Optional[Any] = None) -> dict:
    """失败响应（返回字典格式）
    
    Args:
        code: BizCode 业务错误码
        msg: 错误消息
        error: 详细错误信息
        data: 额外数据
        
    Returns:
        dict: 包含错误信息的字典
    """
    return {
        "code": code,
        "msg": msg,
        "data": data if data is not None else {},
        "error": error,
        "time": int(time.time() * 1000),
    }