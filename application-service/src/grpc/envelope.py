"""HTTP-equivalent envelopes for pre-stream gRPC failures."""

from __future__ import annotations

import json
from typing import Any, NoReturn

from fastapi.encoders import jsonable_encoder
from memorybear_grpc.errors import error_trailers, grpc_status_for_http

from ..api.schemas.common import fail
from ..i18n import translate


def json_bytes(value: Any) -> bytes:
    """Serialize exactly like the HTTP JSON response (FastAPI then Starlette)."""

    return json.dumps(
        jsonable_encoder(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


async def abort_with(context, http_status: int, content: dict[str, Any]) -> NoReturn:
    """Terminate the RPC with the HTTP status and envelope in trailers."""

    await context.abort(
        grpc_status_for_http(http_status),
        str(content.get("msg") or content.get("message") or "chat failed"),
        trailing_metadata=error_trailers(http_status, json_bytes(content)),
    )
    raise AssertionError("grpc abort returned")


def auth_rejection_content(
    http_status: int, detail: str, language: str
) -> dict[str, Any]:
    """The body the HTTP auth middleware renders for the same rejection."""

    return fail(
        code=http_status,
        msg=translate("errors.common.unauthorized", language),
        error=detail,
    )


__all__ = ["abort_with", "auth_rejection_content", "json_bytes"]
