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


def fail(code: int, msg: str, error: str = "", data: Optional[Any] = None) -> JSONResponse:
    """失败响应（根据 BizCode 映射到对应的 HTTP 状态码）
    
    Args:
        code: BizCode 业务错误码
        msg: 错误消息
        error: 详细错误信息
        data: 额外数据
        
    Returns:
        JSONResponse: 包含正确 HTTP 状态码的响应
    """
    # 从 HTTP_MAPPING 获取对应的 HTTP 状态码，默认 400
    http_status = HTTP_MAPPING.get(BizCode(code), 400)
    
    return JSONResponse(
        status_code=http_status,
        content={
            "code": code,
            "msg": msg,
            "data": data if data is not None else {},
            "error": error,
            "time": int(time.time() * 1000),
        }
    )