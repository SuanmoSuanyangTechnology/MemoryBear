"""HTTP <-> gRPC status mapping and error trailer transport.

A gRPC status code cannot carry the full HTTP failure envelope. Pre-stream
failures therefore travel as ``context.abort`` plus trailing metadata that
restores the original HTTP status and body at the gateway.
"""

from __future__ import annotations

from collections.abc import Iterable

import grpc

HTTP_STATUS_TRAILER = "x-http-status"
ERROR_BODY_TRAILER = "x-error-json-bin"

_HTTP_TO_GRPC: dict[int, grpc.StatusCode] = {
    400: grpc.StatusCode.INVALID_ARGUMENT,
    401: grpc.StatusCode.UNAUTHENTICATED,
    403: grpc.StatusCode.PERMISSION_DENIED,
    404: grpc.StatusCode.NOT_FOUND,
    409: grpc.StatusCode.ABORTED,
    429: grpc.StatusCode.RESOURCE_EXHAUSTED,
    503: grpc.StatusCode.UNAVAILABLE,
    504: grpc.StatusCode.DEADLINE_EXCEEDED,
}

_GRPC_TO_HTTP: dict[grpc.StatusCode, int] = {
    grpc.StatusCode.INVALID_ARGUMENT: 400,
    grpc.StatusCode.UNAUTHENTICATED: 401,
    grpc.StatusCode.PERMISSION_DENIED: 403,
    grpc.StatusCode.NOT_FOUND: 404,
    grpc.StatusCode.ABORTED: 409,
    grpc.StatusCode.ALREADY_EXISTS: 409,
    grpc.StatusCode.RESOURCE_EXHAUSTED: 429,
    grpc.StatusCode.UNIMPLEMENTED: 501,
    grpc.StatusCode.UNAVAILABLE: 503,
    grpc.StatusCode.DEADLINE_EXCEEDED: 504,
}


def grpc_status_for_http(status: int) -> grpc.StatusCode:
    if status in _HTTP_TO_GRPC:
        return _HTTP_TO_GRPC[status]
    if status >= 500:
        return grpc.StatusCode.INTERNAL
    if status >= 400:
        return grpc.StatusCode.INVALID_ARGUMENT
    return grpc.StatusCode.UNKNOWN


def http_status_for_grpc(code: grpc.StatusCode) -> int:
    return _GRPC_TO_HTTP.get(code, 500)


def error_trailers(status: int, body: bytes) -> tuple[tuple[str, str | bytes], ...]:
    # grpcio's abort/set_trailing_metadata reject lists; keep the server side
    # call sites free of tuple(...) wrapping.
    return ((HTTP_STATUS_TRAILER, str(status)), (ERROR_BODY_TRAILER, body))


def parse_error_trailers(
    metadata: Iterable[tuple[str, str | bytes]],
) -> tuple[int, bytes] | None:
    raw_status: str | bytes | None = None
    raw_body: bytes | None = None
    for key, value in metadata:
        if key == HTTP_STATUS_TRAILER:
            raw_status = value
        elif key == ERROR_BODY_TRAILER:
            raw_body = value.encode() if isinstance(value, str) else value
    if raw_status is None or raw_body is None:
        return None
    try:
        return int(raw_status), raw_body
    except (TypeError, ValueError):
        return None
