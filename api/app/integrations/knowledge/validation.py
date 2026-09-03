"""Safe validation responses for knowledge retrieval routes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from fastapi.responses import JSONResponse

from app.core.response_utils import fail

_RETRIEVAL_PATHS = frozenset(
    {
        "/api/chunks/retrieval",
        "/v1/chunks/retrieval",
    }
)
_RETRIEVAL_VALIDATION_MESSAGE = "Invalid retrieval request"


def is_retrieval_request_validation_error(
    path: str,
    errors: Sequence[Mapping[str, Any]],
) -> bool:
    del errors
    return path in _RETRIEVAL_PATHS


def safe_retrieval_validation_response() -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content=fail(
            code=400,
            msg=_RETRIEVAL_VALIDATION_MESSAGE,
            error=_RETRIEVAL_VALIDATION_MESSAGE,
        ),
    )


__all__ = [
    "is_retrieval_request_validation_error",
    "safe_retrieval_validation_response",
]
