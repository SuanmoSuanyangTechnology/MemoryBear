"""模型调用失败 → 统一错误面字段（``code``/``msg``/``error``/``retryable``）。

模型服务已归类的失败（``ModelInvokeFailedError``）语义**透传不重编码**：业务码来自
服务侧，``msg`` 用服务侧分级文案（缺失时回落 error-core 目录文案），``error`` 只放
脱敏诊断串。HTTP 出口与 SSE 错误帧共用本模块，避免两处各写一份判定。
"""

from __future__ import annotations

from typing import Any

from error_core.codes import BizCode, code_name, coerce_code
from error_core.diagnostics import format_diagnostics
from error_core.messages import user_message

from app.core.trace import get_trace_id
from app.i18n.exceptions import get_current_locale
from app.integrations.model.errors import ModelInvokeFailedError


def model_failure_fields(exc: ModelInvokeFailedError) -> dict[str, Any]:
    """把模型调用失败摊平成错误面字段；``code`` 永不返回 0。"""

    locale = get_current_locale()
    code = coerce_code(exc.biz_code)
    if code in (None, BizCode.OK.value):
        code = BizCode.INTERNAL_ERROR.value
    return {
        "code": code,
        "code_name": code_name(code),
        "msg": exc.user_msg or user_message(code, locale) or code_name(code),
        "error": format_diagnostics(
            trace_id=get_trace_id() or None,
            detail=exc.detail or str(exc),
        ),
        "retryable": bool(exc.retryable),
        "attempts": int(exc.attempts or 0),
        "channel_id": None if exc.channel_id is None else str(exc.channel_id),
        "locale": locale,
    }


__all__ = ["model_failure_fields"]