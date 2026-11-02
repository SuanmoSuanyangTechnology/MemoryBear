"""Gateway-side gRPC forwarder for the hot chat path (gateway -> application).

The gateway is a gRPC client only. RPC frames are rebuilt into the exact public
SSE stream the HTTP path emits, so clients cannot tell the transports apart.
Pre-stream failures restore the original HTTP status and envelope from trailing
metadata; mid-stream failures end the SSE, matching an upstream disconnect on
the HTTP path.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable

import grpc
from fastapi import Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from memorybear.application.v1 import chat_pb2, chat_pb2_grpc
from memorybear_grpc.client import CLIENT_FRAMES, STREAM_CANCELLED, ChannelPool
from memorybear_grpc.errors import http_status_for_grpc, parse_error_trailers

from .circuit import CircuitBreaker
from .forward import TargetRoute
from .metrics import (
    gateway_circuit_breaker_state,
    gateway_forward_requests_total,
    gateway_upstream_errors_total,
)
from .stream_gate import StreamGate

logger = logging.getLogger(__name__)

CHAT_PATH = "/v1/app/chat"
_HEARTBEAT_FRAME = b": heartbeat\n\n"
_QUEUE_MAXSIZE = 16
_ITEM = "item"
_ERROR = "error"
_END = "end"
_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
_BLOCKED_HEADERS = ("x-api-key", "x-internal-token")


def _ascii_metadata(values: list[tuple[str, str]]) -> tuple[tuple[str, str], ...]:
    """gRPC metadata must be ASCII; drop anything that cannot travel unchanged."""

    safe: list[tuple[str, str]] = []
    for key, value in values:
        try:
            key.encode("ascii")
            value.encode("ascii")
        except UnicodeEncodeError:
            logger.warning("dropping non-ascii grpc metadata key=%s", key)
            continue
        safe.append((key, value))
    return tuple(safe)


def _metadata(request: Request) -> tuple[tuple[str, str], ...]:
    """Identity namespace plus accept-language; credentials never ride x-*.

    The internal token, when issued, is the only authorization source (mirrors
    the HTTP forwarder); x-api-key / x-internal-token never leave the gateway.
    """

    values: list[tuple[str, str]] = []
    language = request.headers.get("accept-language")
    if language:
        values.append(("accept-language", language))
    for name, value in request.headers.items():
        low = name.lower()
        if low.startswith("x-") and low not in _BLOCKED_HEADERS:
            values.append((low, value))
    internal = getattr(request.state, "internal_token", None)
    if internal:
        values.append(("authorization", f"Bearer {internal}"))
    return _ascii_metadata(values)


def _render_event(frame: chat_pb2.EventFrame) -> bytes:
    return b"event: " + frame.event.encode() + b"\ndata: " + frame.data_json + b"\n\n"


class _SSEStream:
    """Async iterator that always runs the stream shutdown.

    ``aclose()`` on a never-started async generator does not execute its body,
    so a plain generator's ``finally`` would leak the gate slot when the
    response is closed before the first read.
    """

    def __init__(
        self, generator: AsyncIterator[bytes], shutdown: Callable[[], Awaitable[None]]
    ) -> None:
        self._generator = generator
        self._shutdown = shutdown

    def __aiter__(self) -> _SSEStream:
        return self

    async def __anext__(self) -> bytes:
        try:
            return await self._generator.__anext__()
        except StopAsyncIteration:
            await self._shutdown()
            raise

    async def aclose(self) -> None:
        with contextlib.suppress(BaseException):
            await self._generator.aclose()
        await self._shutdown()


class GrpcForwarder:
    def __init__(
        self,
        *,
        pool: ChannelPool,
        gate: StreamGate,
        circuit: CircuitBreaker | None = None,
        protocol_override: str = "",
        default_target: str = "",
        deadline_s: float = 3600.0,
        heartbeat_s: float = 30.0,
        idle_timeout_s: float = 300.0,
    ) -> None:
        self._pool = pool
        self._gate = gate
        self._circuit = circuit or CircuitBreaker()
        self._protocol_override = protocol_override
        self._default_target = default_target
        self._deadline_s = deadline_s
        self._heartbeat_s = heartbeat_s
        self._idle_timeout_s = idle_timeout_s

    def matches(self, request: Request, route: TargetRoute) -> bool:
        """Protocol switch: an explicit env value wins; otherwise the route
        default decides (routes.json may opt a path into gRPC)."""

        if request.method != "POST" or request.url.path != CHAT_PATH:
            return False
        if route.service != "application-service":
            return False
        if self._protocol_override == "http":
            return False
        if self._protocol_override == "grpc":
            return True
        return route.protocol == "grpc"

    async def forward(self, request: Request, route: TargetRoute) -> Response:
        if not route.grpc_method:
            return JSONResponse(
                status_code=500, content={"detail": "grpc route misconfigured"}
            )
        # The breaker gates new calls only; established streams are unaffected.
        if self._circuit.is_open():
            gateway_circuit_breaker_state.labels(target=route.service).set(1)
            return JSONResponse(status_code=502, content={"detail": "circuit open"})
        gateway_circuit_breaker_state.labels(target=route.service).set(0)
        body = await request.body()
        target = route.grpc_target or self._default_target
        channel = await self._pool.channel(target)
        stub = chat_pb2_grpc.AppChatServiceStub(channel)
        call = stub.Chat(
            chat_pb2.ChatRequest(body_json=body),
            metadata=_metadata(request),
            timeout=self._deadline_s,
            wait_for_ready=False,
        )
        # The first frame decides the response shape (ResultFrame -> buffered
        # JSON, EventFrame -> SSE); failures before it can still set the HTTP
        # status, failures after it can only end the stream.
        try:
            first = await call.read()
        except grpc.aio.AioRpcError as exc:
            return self._pre_stream_failure(route, exc)
        if first is grpc.aio.EOF:
            call.cancel()
            return self._transport_failure(route, "connect", 502, "upstream unavailable")
        if first.seq != 0:
            call.cancel()
            logger.warning("grpc chat stream started at seq=%s", first.seq)
            return JSONResponse(
                status_code=502, content={"detail": "upstream protocol error"}
            )
        kind = first.WhichOneof("payload")
        if kind == "result":
            CLIENT_FRAMES.labels(route.service, "result").inc()
            self._answered(route, first.result.http_status or 200)
            frame = first.result
            return Response(
                content=frame.body_json,
                status_code=frame.http_status or 200,
                media_type="application/json",
                headers={k.lower(): v for k, v in frame.headers.items()},
            )
        if kind != "event":
            call.cancel()
            logger.warning("grpc chat stream started with an unknown frame: %s", kind)
            return JSONResponse(
                status_code=502, content={"detail": "upstream protocol error"}
            )
        CLIENT_FRAMES.labels(route.service, "event").inc()
        self._answered(route, 200)
        return await self._stream_response(route, call, first.event)

    def _answered(self, route: TargetRoute, status: int) -> None:
        gateway_forward_requests_total.labels(
            target=route.service, status_class=f"{status // 100}xx"
        ).inc()
        self._circuit.record_success()

    def _transport_failure(
        self, route: TargetRoute, error_type: str, status: int, detail: str
    ) -> Response:
        gateway_upstream_errors_total.labels(
            target=route.service, error_type=error_type
        ).inc()
        self._circuit.record_failure()
        return JSONResponse(status_code=status, content={"detail": detail})

    def _pre_stream_failure(
        self, route: TargetRoute, exc: grpc.aio.AioRpcError
    ) -> Response:
        parsed = parse_error_trailers(exc.trailing_metadata() or ())
        if parsed is not None:
            status, raw = parsed
            # The application answered: the transport is healthy even though
            # the request failed (same rule as the HTTP forwarder's 4xx path).
            self._answered(route, status)
            return Response(content=raw, status_code=status, media_type="application/json")
        if exc.code() == grpc.StatusCode.DEADLINE_EXCEEDED:
            return self._transport_failure(route, "timeout", 504, "upstream timeout")
        if exc.code() == grpc.StatusCode.UNAVAILABLE:
            return self._transport_failure(route, "connect", 502, "upstream unavailable")
        status = http_status_for_grpc(exc.code())
        self._answered(route, status)
        logger.warning("grpc chat call failed before the first frame: %s", exc.code().name)
        return JSONResponse(status_code=status, content={"detail": "upstream error"})

    async def _stream_response(
        self, route: TargetRoute, call, first: chat_pb2.EventFrame
    ) -> Response:
        if not await self._gate.acquire():
            call.cancel()
            return JSONResponse(status_code=503, content={"detail": "too many streams"})
        queue: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        pump = asyncio.create_task(self._pump(call, queue, route.service, next_seq=1))
        released = False

        async def shutdown() -> None:
            nonlocal released
            if released:
                return
            released = True
            try:
                if not pump.done():
                    pump.cancel()
                with contextlib.suppress(BaseException):
                    await pump
                call.cancel()
            finally:
                await self._gate.release()

        async def body_iter() -> AsyncIterator[bytes]:
            try:
                try:
                    yield _render_event(first)
                    idle = 0.0
                    while True:
                        try:
                            kind, payload = await asyncio.wait_for(
                                queue.get(), timeout=self._heartbeat_s
                            )
                        except TimeoutError:
                            idle += self._heartbeat_s
                            if idle >= self._idle_timeout_s:
                                logger.warning(
                                    "grpc chat stream idle for %.0fs (limit %.0fs), closing",
                                    idle,
                                    self._idle_timeout_s,
                                )
                                break
                            yield _HEARTBEAT_FRAME
                            continue
                        idle = 0.0
                        if kind == _ITEM:
                            yield _render_event(payload)
                            continue
                        if kind == _ERROR:
                            # Mid-stream failure ends the SSE exactly like an
                            # upstream disconnect on the HTTP path.
                            logger.warning(
                                "grpc chat stream failed mid-flight: %s", payload
                            )
                            break
                        break
                except (asyncio.CancelledError, GeneratorExit):
                    STREAM_CANCELLED.labels(route.service, "client").inc()
                    raise
            finally:
                await shutdown()

        return StreamingResponse(
            _SSEStream(body_iter(), shutdown),
            status_code=200,
            media_type="text/event-stream",
            headers=dict(_SSE_HEADERS),
        )

    async def _pump(
        self, call, queue: asyncio.Queue, service: str, *, next_seq: int
    ) -> None:
        """Read frames in one task: reads never overlap and the consumer can
        emit heartbeats while the upstream stays silent."""

        seq = next_seq
        try:
            while True:
                frame = await call.read()
                if frame is grpc.aio.EOF:
                    await queue.put((_END, None))
                    return
                CLIENT_FRAMES.labels(
                    service, frame.WhichOneof("payload") or "none"
                ).inc()
                if frame.seq != seq:
                    await queue.put((_ERROR, f"seq {frame.seq} != {seq}"))
                    return
                if frame.WhichOneof("payload") != "event":
                    await queue.put((_ERROR, "non-event frame inside a stream"))
                    return
                seq += 1
                await queue.put((_ITEM, frame.event))
        except asyncio.CancelledError:
            raise
        except grpc.aio.AioRpcError as exc:
            await queue.put((_ERROR, exc.code().name))
        except Exception:  # noqa: BLE001 - surfaced to the consumer
            logger.exception("grpc chat stream pump failed")
            await queue.put((_ERROR, "internal"))


__all__ = ["CHAT_PATH", "GrpcForwarder"]
