"""gRPC invoke transport: the binary counterpart of the SSE frame contract.

The service streams typed frames over ``ModelInvokeService.Invoke``; payloads
stay JSON so the semantics (frame order, seq, error vocabulary) have exactly
one definition. Only the carriage changes.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from typing import Any

import grpc
from memorybear.model.v1 import invoke_pb2, invoke_pb2_grpc
from memorybear_grpc.client import CLIENT_FRAMES, STREAM_CANCELLED, ChannelPool
from memorybear_grpc.errors import http_status_for_grpc, parse_error_trailers
from memorybear_grpc.metadata import deadline_for, upstream_remaining

from redbear_model.errors import (
    RemoteInvokeError,
    RemoteInvokeFailedError,
    RemoteInvokeIdleTimeoutError,
    RemoteInvokeProtocolError,
    RemoteInvokeUnavailableError,
)

from .remote import (
    DONE_STATUSES,
    AsyncInvokeTransport,
    InvokeChunkFrame,
    InvokeDoneFrame,
    InvokeFrame,
    InvokeRequest,
    InvokeResultFrame,
    InvokeTarget,
    InvokeTimeouts,
    InvokeUsageFrame,
    envelope_failure,
)

_METADATA_KEY_RE = re.compile(r"^[a-z0-9_.-]+$")


def _ascii_metadata(headers: dict[str, str]) -> tuple[tuple[str, str], ...]:
    """gRPC metadata keys are lowercase ASCII; drop anything that cannot travel."""

    values: list[tuple[str, str]] = []
    for key, value in headers.items():
        lowered = key.lower()
        if not _METADATA_KEY_RE.match(lowered):
            continue
        try:
            value.encode("ascii")
        except UnicodeEncodeError:
            continue
        values.append((lowered, value))
    return tuple(values)


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def _loads_json(raw: bytes, kind: str) -> Any:
    try:
        return json.loads(raw)
    except (UnicodeDecodeError, ValueError) as exc:
        raise RemoteInvokeProtocolError(f"malformed {kind} frame JSON: {exc}") from exc


class _Sequence:
    """Frames carry one global, gap-free counter starting at zero."""

    def __init__(self) -> None:
        self._next = 0

    def check(self, frame: Any) -> None:
        if frame.seq != self._next:
            raise RemoteInvokeProtocolError(
                f"frame seq {frame.seq} != expected {self._next}"
            )
        self._next += 1


class GrpcAsyncInvokeTransport(AsyncInvokeTransport):
    """Async invoke transport over a pooled gRPC channel.

    Subclasses ``AsyncInvokeTransport`` for the adapter's isinstance check
    only; no HTTP state is inherited (``__init__`` is not chained).
    """

    def __init__(
        self,
        pool: ChannelPool,
        *,
        target_name: str,
        timeouts: InvokeTimeouts | None = None,
        call_budget_s: float = 180.0,
        margin_s: float = 30.0,
    ) -> None:
        self._pool = pool
        self._target_name = target_name
        self._timeouts = timeouts or InvokeTimeouts()
        self._call_budget_s = call_budget_s
        self._margin_s = margin_s

    def _stream_deadline(self) -> float | None:
        """Streams are idle-bounded; only an upstream deadline caps their total."""

        remaining = upstream_remaining()
        if remaining is None:
            return None
        return max(0.001, remaining - self._margin_s)

    def _call_deadline(self) -> float:
        return deadline_for(budget_s=self._call_budget_s, margin_s=self._margin_s)

    async def _open(
        self, request: InvokeRequest, target: InvokeTarget, *, stream: bool
    ) -> Any:
        channel = await self._pool.channel(self._target_name)
        stub = invoke_pb2_grpc.ModelInvokeServiceStub(channel)
        return stub.Invoke(
            invoke_pb2.InvokeRequest(
                config_id=str(request.config_id),
                type=request.type.value,
                stream=stream,
                params_json=_json_bytes(request.params),
            ),
            metadata=_ascii_metadata(target.headers()),
            timeout=self._stream_deadline() if stream else self._call_deadline(),
            wait_for_ready=False,
        )

    async def _read(self, call: Any) -> Any:
        try:
            return await asyncio.wait_for(call.read(), timeout=self._timeouts.idle_s)
        except TimeoutError as exc:
            raise RemoteInvokeIdleTimeoutError(self._timeouts.idle_s) from exc
        except grpc.aio.AioRpcError as exc:
            raise self._rpc_error(exc) from exc

    def _rpc_error(self, exc: grpc.aio.AioRpcError) -> RemoteInvokeError:
        parsed = parse_error_trailers(exc.trailing_metadata() or ())
        if parsed is not None:
            status, raw = parsed
            try:
                payload = json.loads(raw)
            except (UnicodeDecodeError, ValueError):
                payload = None
            if isinstance(payload, dict):
                return envelope_failure(payload, status)
        if exc.code() == grpc.StatusCode.DEADLINE_EXCEEDED:
            return RemoteInvokeUnavailableError("remote invoke deadline exceeded")
        status = http_status_for_grpc(exc.code())
        if 400 <= status < 500:
            # Interceptors abort without an HTTP envelope; keep 4xx failures
            # distinguishable instead of collapsing them into 503.
            return RemoteInvokeFailedError(
                code=None, message=exc.details() or "", http_status=status
            )
        return RemoteInvokeUnavailableError(
            f"remote invoke rpc failed: {exc.code().name}"
        )

    def _decode(self, frame: Any) -> InvokeFrame:
        kind = frame.WhichOneof("payload")
        if kind == "chunk":
            message = _loads_json(frame.chunk.message_json, "chunk")
            if not isinstance(message, dict):
                raise RemoteInvokeProtocolError("chunk frame payload must be an object")
            return InvokeChunkFrame(message=message, seq=frame.seq)
        if kind == "result":
            return InvokeResultFrame(data=_loads_json(frame.result.data_json, "result"))
        if kind == "usage":
            event = _loads_json(frame.usage.event_json, "usage")
            if not isinstance(event, dict):
                raise RemoteInvokeProtocolError("usage frame payload must be an object")
            return InvokeUsageFrame(event=event)
        if kind == "done":
            if frame.done.status not in DONE_STATUSES:
                raise RemoteInvokeProtocolError(
                    f"unknown done status: {frame.done.status!r}"
                )
            return InvokeDoneFrame(status=frame.done.status)
        if kind == "error":
            raise RemoteInvokeFailedError(
                code=frame.error.code,
                message=frame.error.message,
                retryable=frame.error.retryable,
            )
        raise RemoteInvokeProtocolError("invoke frame carries no payload")

    async def stream(
        self, request: InvokeRequest, target: InvokeTarget
    ) -> AsyncIterator[InvokeFrame]:
        call = await self._open(request, target, stream=True)
        sequence = _Sequence()
        try:
            while True:
                frame = await self._read(call)
                if frame is grpc.aio.EOF:
                    raise RemoteInvokeProtocolError(
                        "stream ended before the done frame"
                    )
                CLIENT_FRAMES.labels(
                    self._target_name, frame.WhichOneof("payload") or "none"
                ).inc()
                sequence.check(frame)
                decoded = self._decode(frame)
                yield decoded
                if isinstance(decoded, InvokeDoneFrame):
                    return
        except (asyncio.CancelledError, GeneratorExit):
            STREAM_CANCELLED.labels(self._target_name, "client").inc()
            raise
        finally:
            call.cancel()

    async def call(self, request: InvokeRequest, target: InvokeTarget) -> Any:
        call = await self._open(request, target, stream=False)
        sequence = _Sequence()
        has_result = False
        result: Any = None
        try:
            while True:
                frame = await self._read(call)
                if frame is grpc.aio.EOF:
                    raise RemoteInvokeProtocolError(
                        "stream ended before the done frame"
                    )
                CLIENT_FRAMES.labels(
                    self._target_name, frame.WhichOneof("payload") or "none"
                ).inc()
                sequence.check(frame)
                decoded = self._decode(frame)
                if isinstance(decoded, InvokeChunkFrame):
                    raise RemoteInvokeProtocolError(
                        "non-stream invoke carried a chunk frame"
                    )
                if isinstance(decoded, InvokeResultFrame):
                    if has_result:
                        raise RemoteInvokeProtocolError(
                            "invoke carried more than one result frame"
                        )
                    result = decoded.data
                    has_result = True
                elif isinstance(decoded, InvokeDoneFrame):
                    if not has_result:
                        raise RemoteInvokeProtocolError(
                            "done frame arrived before the result frame"
                        )
                    return result
        except (asyncio.CancelledError, GeneratorExit):
            STREAM_CANCELLED.labels(self._target_name, "client").inc()
            raise
        finally:
            call.cancel()


__all__ = ["GrpcAsyncInvokeTransport"]
