"""HTTP-equivalent envelopes for gRPC failures.

A gRPC status code cannot carry the full HTTP failure envelope. Failures
therefore travel as ``context.abort`` plus trailing metadata that restores the
original HTTP status and body on the client, mirroring the chat and model invoke
contracts.
"""

from __future__ import annotations

import json
from typing import Any, NoReturn

from memorybear_grpc.errors import error_trailers, grpc_status_for_http

import grpc

from ..api.schemas.common import fail, success
from ..errors import KnowledgeError
from ..i18n import translate

TRACE_ID_TRAILER = "x-trace-id"

# Statuses the gRPC authenticator can raise before any business handling.
_AUTH_ERROR_CODES: dict[int, str] = {
    401: "KB_HTTP_UNAUTHORIZED",
    403: "KB_HTTP_FORBIDDEN",
    500: "KB_INTERNAL_ERROR",
}


def json_bytes(value: Any) -> bytes:
    """Serialize exactly like the HTTP JSON response (FastAPI then Starlette)."""

    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def success_body(data: Any, msg: str) -> bytes:
    return json_bytes(success(data=data, msg=msg))


def error_content(error: KnowledgeError, language: str) -> dict[str, Any]:
    """The body the HTTP error handler renders for the same failure."""

    message = translate(error.code, language, error.params)
    return fail(code=error.response_code, msg=message, error=message)


def _trailers(http_status: int, content: dict[str, Any], trace_id: str) -> tuple:
    return (
        *error_trailers(http_status, json_bytes(content)),
        (TRACE_ID_TRAILER, trace_id),
    )


async def abort_with_error(
    context: grpc.aio.ServicerContext,
    error: KnowledgeError,
    *,
    language: str,
    trace_id: str,
) -> NoReturn:
    content = error_content(error, language)
    message = str(content.get("msg") or "knowledge request failed")
    await context.abort(
        grpc_status_for_http(error.status_code),
        message,
        trailing_metadata=_trailers(error.status_code, content, trace_id),
    )
    raise AssertionError("grpc abort returned")


async def abort_with_status(
    context: grpc.aio.ServicerContext,
    http_status: int,
    detail: str,
    *,
    language: str,
    trace_id: str,
) -> NoReturn:
    """Reject a call before routing with the HTTP-equivalent status and envelope."""

    key = _AUTH_ERROR_CODES.get(http_status, "KB_PRINCIPAL_INVALID")
    content = fail(code=http_status, msg=translate(key, language), error=detail)
    await context.abort(
        grpc_status_for_http(http_status),
        detail,
        trailing_metadata=_trailers(http_status, content, trace_id),
    )
    raise AssertionError("grpc abort returned")


__all__ = [
    "TRACE_ID_TRAILER",
    "abort_with_error",
    "abort_with_status",
    "error_content",
    "json_bytes",
    "success_body",
]