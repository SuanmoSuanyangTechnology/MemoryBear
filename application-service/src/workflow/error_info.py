"""Workflow exception classification for the unified error contract."""

from __future__ import annotations

from typing import Any

from error_core.codes import (
    HTTP_STATUS_TO_BIZ_CODE,
    BizCode,
    code_name,
    coerce_code,
    http_status_for,
    is_known_code,
)
from error_core.diagnostics import format_diagnostics
from error_core.knowledge import knowledge_message
from error_core.messages import fallback_message, user_message

from ..errors import BusinessException
from ..i18n import get_current_locale, translate
from ..integrations.knowledge.errors import KnowledgeServiceError
from ..integrations.model.errors import ModelInvokeFailedError
from ..trace import get_trace_id


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

    # A failure surface must never present OK (0): an exception carrying
    # BizCode.OK is mislabelled and is reported as an unclassified failure.
    if isinstance(exc, ModelInvokeFailedError):
        code = coerce_code(exc.biz_code)
        if code in (None, BizCode.OK.value):
            code = BizCode.INTERNAL_ERROR.value
        msg = getattr(exc, "user_msg", None) or user_message(
            code, locale
        ) or fallback_message(http_status_for(code), locale)
        detail = getattr(exc, "detail", "") or str(exc) or code_name(code)
        retryable = bool(exc.retryable)
    elif isinstance(exc, KnowledgeServiceError):
        code = (
            exc.code
            if exc.code != BizCode.OK and is_known_code(exc.code)
            else HTTP_STATUS_TO_BIZ_CODE.get(exc.status_code, BizCode.INTERNAL_ERROR).value
        )
        msg = knowledge_message(code, locale)
        detail = (
            f"upstream_status={exc.status_code} upstream_code={exc.code} "
            f"upstream_trace_id={exc.trace_id}: {exc.diagnostic or exc}"
        )
        status = exc.status_code if exc.status_code >= 400 else http_status_for(code)
        retryable = status == 429 or status >= 500
    elif isinstance(exc, BusinessException):
        code = coerce_code(exc.code)
        if code in (None, BizCode.OK.value):
            code = BizCode.INTERNAL_ERROR.value
        msg = user_message(code, locale) or fallback_message(
            http_status_for(code), locale
        )
        field = exc.context.get("field")
        if code == BizCode.MISSING_PARAMETER and isinstance(field, str) and field:
            msg = translate("errors.validation.missing_field", locale, field=field)
        detail = exc.message or code_name(code)
        retryable = http_status_for(code) >= 500
    else:
        code = BizCode.INTERNAL_ERROR.value
        msg = user_message(code, locale) or "服务内部错误，请稍后重试"
        detail = (
            fallback_detail
            or (f"{type(exc).__name__}: {exc}" if exc is not None else "")
            or code_name(code)
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
