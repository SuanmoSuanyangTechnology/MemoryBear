"""Safe validation responses for knowledge retrieval routes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from error_core.codes import BizCode
from error_core.diagnostics import format_diagnostics
from error_core.knowledge import knowledge_message, validation_diagnostic
from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.response_utils import fail

_RETRIEVAL_PATHS = frozenset(
    {
        "/api/chunks/retrieval",
        "/v1/chunks/retrieval",
    }
)


def is_retrieval_request_validation_error(
    path: str,
    errors: Sequence[Mapping[str, Any]],
) -> bool:
    del errors
    return path in _RETRIEVAL_PATHS


def safe_retrieval_validation_response(
    request: Request, errors: Sequence[Mapping[str, Any]],
) -> JSONResponse:
    trace_id = getattr(request.state, "trace_id", None)
    language = getattr(request.state, "language", "zh")
    return JSONResponse(
        status_code=422,
        headers={"X-Trace-Id": trace_id} if trace_id else {},
        content=fail(
            code=BizCode.VALIDATION_FAILED.value,
            msg=knowledge_message(BizCode.VALIDATION_FAILED.value, language),
            error=format_diagnostics(
                trace_id=trace_id, detail=validation_diagnostic(errors),
            ),
        ),
    )


__all__ = [
    "is_retrieval_request_validation_error",
    "safe_retrieval_validation_response",
]
