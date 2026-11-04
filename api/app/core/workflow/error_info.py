"""Workflow exception classification for the unified error contract."""

from __future__ import annotations

from typing import Any

from error_core.codes import BizCode, code_name, coerce_code, http_status_for
from error_core.diagnostics import format_diagnostics
from error_core.messages import user_message

from app.core.exceptions import BusinessException
from app.core.trace import get_trace_id
from app.i18n.exceptions import get_current_locale
from app.integrations.model.errors import ModelInvokeFailedError


def workflow_error_info(
    exc: BaseException | None,
    *,
    fallback_detail: str = "",
) -> dict[str, Any]:
    """Map a workflow failure to the unified ``code/msg/error/retryable`` fields.

    ``msg`` is the user-facing, locale-aware catalog message; ``error`` carries
    redacted diagnostics (trace id + detail) and must never be rendered to end
    users. ``exc`` may be ``None`` for synthetic failures that only carry a
    diagnostic string.
    """

    locale = get_current_locale()
    trace_id = get_trace_id() or None

    if isinstance(exc, ModelInvokeFailedError):
        code = coerce_code(exc.biz_code) or BizCode.INTERNAL_ERROR.value
        msg = getattr(exc, "user_msg", None) or user_message(code, locale) or str(exc)
        detail = getattr(exc, "detail", "") or str(exc)
        retryable = bool(exc.retryable)
    elif isinstance(exc, BusinessException):
        code = coerce_code(exc.code) or BizCode.INTERNAL_ERROR.value
        msg = user_message(code, locale) or exc.message
        detail = exc.message
        retryable = http_status_for(code) >= 500
    else:
        code = BizCode.INTERNAL_ERROR.value
        msg = user_message(code, locale) or "服务内部错误，请稍后重试"
        detail = fallback_detail or (
            f"{type(exc).__name__}: {exc}" if exc is not None else ""
        )
        retryable = http_status_for(code) >= 500

    return {
        "code": code,
        "code_name": code_name(code),
        "msg": msg,
        "error": format_diagnostics(trace_id=trace_id, detail=detail),
        "retryable": retryable,
        "locale": locale,
    }


__all__ = ["workflow_error_info"]
