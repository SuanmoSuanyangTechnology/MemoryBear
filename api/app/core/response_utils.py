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
    """失败响应（返回JSONResponse，包含正确的HTTP状态码）
    
    Args:
        code: BizCode 业务错误码
        msg: 错误消息
        error: 详细错误信息
        data: 额外数据
        
    Returns:
        JSONResponse: 包含错误信息和正确HTTP状态码的响应
    """
    # 根据业务错误码获取对应的HTTP状态码
    http_status = HTTP_MAPPING.get(code, 500)  # 默认500
    
    response_body = {
        "code": code,
        "msg": msg,
        "data": data if data is not None else {},
        "error": error,
        "time": int(time.time() * 1000),
    }
    
    return JSONResponse(
        status_code=http_status,
        content=response_body
    )