"""统一错误信封：HTTP 状态由业务码派生，``error`` 保留原 detail 文案。"""
from __future__ import annotations

from typing import Any

from error_core import BizCode, http_status_for
from fastapi.responses import JSONResponse


def error_response(code: BizCode, message: str, **extra: Any) -> JSONResponse:
    """Render ``{code, msg, error}``; legacy extra keys pass through untouched."""
    return JSONResponse(
        status_code=http_status_for(code),
        content={"code": int(code), "msg": message, "error": message, **extra},
    )
