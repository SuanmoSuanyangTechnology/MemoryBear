"""Unified SSE error payloads for single/multi-agent, handoff and prompt-optimizer."""

from __future__ import annotations

import json
import logging
from typing import Any

from error_core.codes import BizCode, code_name, coerce_code, http_status_for
from error_core.diagnostics import format_diagnostics, redact
from error_core.envelope import error_frame as build_error_frame
from error_core.messages import user_message

from ..errors import BusinessException
from ..integrations.model.errors import ModelInvokeFailedError

logger = logging.getLogger(__name__)


def error_payload(
    exc: BaseException,
    *,
    locale: str | None = None,
    trace_id: str | None = None,
    fallback_code: BizCode = BizCode.INTERNAL_ERROR,
) -> dict[str, Any]:
    # Error frames must never carry OK (0); a mislabelled code is treated as
    # unresolved and falls back to the caller's failure code.
    if isinstance(exc, ModelInvokeFailedError):
        numeric = coerce_code(exc.biz_code)
        if numeric in (None, BizCode.OK.value):
            numeric = fallback_code.value
        msg = getattr(exc, "user_msg", None) or user_message(numeric, locale) or str(exc)
        # The upstream_service suffix is appended once by
        # integrations/model/invoke.py to_host_error (Task 18); do not repeat it.
        detail = redact(getattr(exc, "detail", "") or str(exc))
        retryable = bool(exc.retryable)
        attempts = exc.attempts or 0
        channel_id = str(exc.channel_id) if exc.channel_id else None
    elif isinstance(exc, BusinessException):
        numeric = coerce_code(exc.code)
        if numeric in (None, BizCode.OK.value):
            numeric = fallback_code.value
        msg = user_message(numeric, locale) or exc.message
        detail = format_diagnostics(trace_id=trace_id, detail=exc.message)
        retryable = http_status_for(numeric) >= 500
        attempts = 0
        channel_id = None
    else:
        numeric = fallback_code.value
        msg = user_message(numeric, locale) or str(exc)
        detail = format_diagnostics(
            trace_id=trace_id,
            detail=f"{type(exc).__name__}: {exc}",
        )
        retryable = http_status_for(numeric) >= 500
        attempts = 0
        channel_id = None
    logger.log(
        logging.ERROR if http_status_for(numeric) >= 500 else logging.WARNING,
        "app_error_frame code=%s code_name=%s msg_locale=%s attempts=%s "
        "retryable=%s source_service=application-service trace_id=%s",
        numeric,
        code_name(numeric),
        locale,
        attempts,
        retryable,
        trace_id,
    )
    return build_error_frame(
        code=numeric,
        msg=msg,
        error=detail,
        retryable=retryable,
        attempts=attempts,
        channel_id=channel_id,
        retry_after_seconds=getattr(exc, "retry_after_seconds", None),
    )


def error_frame(payload: dict[str, Any]) -> str:
    return f"event: error\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def legacy_end_error_frame(payload: dict[str, Any]) -> str:
    return f"event: end\ndata: {json.dumps({'error': payload['msg']}, ensure_ascii=False)}\n\n"


__all__ = ["error_frame", "error_payload", "legacy_end_error_frame"]
