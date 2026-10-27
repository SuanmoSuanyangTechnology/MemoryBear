"""ModelInvokeService adapter: typed carriage for the shared invoke event stream.

The event semantics (order, seq, error vocabulary) have exactly one definition
in ``services.invoke_emitter``; this layer only turns events into proto frames
and converts pre-stream failures into gRPC status + HTTP-restoring trailers.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Iterable
from typing import Any

from memorybear.model.v1 import invoke_pb2, invoke_pb2_grpc
from memorybear_grpc.errors import error_trailers, grpc_status_for_http
from memorybear_grpc.interceptors import current_principal
from memorybear_grpc.metadata import bind_upstream_remaining, reset_upstream_remaining
from memorybear_grpc.request import MetadataRequest
from pydantic import TypeAdapter, ValidationError

from ..api.dependencies import InvokePrincipal
from ..api.schemas.common import fail
from ..errors import BizCode
from ..runtime import ProcessRuntime
from ..schemas.invoke_schema import InvokeRequestBody
from ..services.invoke_attribution import attribution_from_headers
from ..services.invoke_emitter import (
    EVENT_CHUNK,
    EVENT_DONE,
    EVENT_ERROR,
    EVENT_RESULT,
    EVENT_USAGE,
    InvokeEvent,
    chunk_events,
    internal_error_event,
    result_events,
    stream_events,
)
from ..services.invoke_runner import build_invoke_plan, guarded
from ..services.invoke_service import InvokeFailure, invoke
from ..trace import TRACE_ID_HEADER, normalize_trace_id, reset_trace_id, set_trace_id
from .auth import INVOKE_ACL_METHOD, INVOKE_ACL_PATH

logger = logging.getLogger(__name__)

_BODY_ADAPTER: TypeAdapter[InvokeRequestBody] = TypeAdapter(InvokeRequestBody)
_VALIDATION_FALLBACK = "请求参数校验失败"


class _BadRequestBody(Exception):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def _decode_body(frame: invoke_pb2.InvokeRequest) -> InvokeRequestBody:
    """Proto envelope + params JSON -> the same request object the HTTP route builds."""
    try:
        params = json.loads(frame.params_json)
    except (UnicodeDecodeError, ValueError) as exc:
        raise _BadRequestBody("JSON decode error") from exc
    if not isinstance(params, dict):
        raise _BadRequestBody("params_json 必须是 JSON 对象")
    try:
        return _BODY_ADAPTER.validate_python(
            {
                "config_id": frame.config_id,
                "type": frame.type,
                "stream": frame.stream,
                "params": params,
            }
        )
    except ValidationError as exc:
        detail = (
            "; ".join(
                str(error.get("msg") or "").removeprefix("Value error, ")
                for error in exc.errors()
            )
            or _VALIDATION_FALLBACK
        )
        raise _BadRequestBody(detail) from exc


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def _to_frame(event: InvokeEvent, seq: int) -> invoke_pb2.InvokeFrame | None:
    """One event -> one typed frame; unknown kinds never reach this layer."""
    frame = invoke_pb2.InvokeFrame(seq=seq)
    if event.kind == EVENT_CHUNK:
        frame.chunk.message_json = _json_bytes(event.payload.get("message"))
    elif event.kind == EVENT_RESULT:
        frame.result.data_json = _json_bytes(event.payload.get("data"))
    elif event.kind == EVENT_USAGE:
        frame.usage.event_json = _json_bytes(event.payload)
    elif event.kind == EVENT_ERROR:
        frame.error.code = str(event.payload.get("code") or "")
        frame.error.message = str(event.payload.get("message") or "")
        frame.error.retryable = bool(event.payload.get("retryable"))
    elif event.kind == EVENT_DONE:
        frame.done.status = str(event.payload.get("status") or "")
    else:
        return None
    return frame


async def _abort_with(context, http_status: int, content: dict[str, Any]) -> None:
    await context.abort(
        grpc_status_for_http(http_status),
        str(content.get("msg") or "invoke failed"),
        trailing_metadata=error_trailers(http_status, _json_bytes(content)),
    )


async def _emit(events: Iterable[InvokeEvent]) -> AsyncIterator[InvokeEvent]:
    for event in events:
        yield event


async def _frames(
    events: AsyncIterator[InvokeEvent],
) -> AsyncIterator[invoke_pb2.InvokeFrame]:
    """Global, gap-free seq starting at zero; closing this closes the event source.

    Frame encoding sits outside the event-source guard, so a facade contract
    breach (non-JSON payload) becomes a terminal error frame here instead of
    escaping as a truncated stream, mirroring the HTTP SSE renderer.
    """
    seq = 0
    try:
        async for event in events:
            try:
                frame = _to_frame(event, seq)
            except Exception as exc:
                logger.exception("invoke frame encode crashed: %s", type(exc).__name__)
                yield _to_frame(internal_error_event(), seq)
                break
            if frame is None:
                continue
            seq += 1
            yield frame
    finally:
        await events.aclose()


class InvokeServicer(invoke_pb2_grpc.ModelInvokeServiceServicer):
    """One servicer per process; the runtime is injected at server build time."""

    def __init__(self, runtime: ProcessRuntime) -> None:
        self._runtime = runtime

    async def Invoke(
        self, request: invoke_pb2.InvokeRequest, context
    ) -> AsyncIterator[invoke_pb2.InvokeFrame]:
        token = bind_upstream_remaining(context.time_remaining())
        try:
            view = MetadataRequest(
                context.invocation_metadata(),
                method=INVOKE_ACL_METHOD,
                path=INVOKE_ACL_PATH,
            )
            # Same contract as the HTTP middleware: a missing or malformed trace
            # ID becomes a generated one, and the service ContextVar is bound so
            # usage attribution and logs agree across both transports.
            trace_id = normalize_trace_id(view.headers.get(TRACE_ID_HEADER))
            trace_token = set_trace_id(trace_id)
            try:
                principal = current_principal()
                if not isinstance(principal, InvokePrincipal):
                    await _abort_with(
                        context,
                        401,
                        fail(
                            BizCode.UNAUTHORIZED.value,
                            "未认证的调用方",
                            error="missing principal",
                        ),
                    )
                try:
                    body = _decode_body(request)
                except _BadRequestBody as exc:
                    await _abort_with(
                        context,
                        422,
                        fail(
                            BizCode.VALIDATION_FAILED.value,
                            exc.detail,
                            error=exc.detail,
                        ),
                    )
                try:
                    attribution = attribution_from_headers(
                        view.headers,
                        source=principal.source,
                        trace_id=trace_id,
                    )
                except InvokeFailure as failure:
                    await _abort_with(
                        context,
                        failure.http_status,
                        fail(failure.code.value, failure.message, error=failure.message),
                    )
                async with self._runtime.database.async_session() as db:
                    plan = build_invoke_plan(
                        invoke,
                        db,
                        settings=self._runtime.settings,
                        tenant_id=principal.tenant_id,
                        request=body,
                        attribution=attribution,
                        runtime=self._runtime,
                    )
                    if plan.chunk_sink is not None:
                        task = asyncio.ensure_future(plan.call())
                        events = chunk_events(task, plan.chunk_sink)
                    elif body.stream:
                        task = asyncio.ensure_future(
                            guarded(plan.call, timeout_s=plan.total_timeout_s)
                        )
                        events = stream_events(task)
                    else:
                        try:
                            outcome = await guarded(
                                plan.call, timeout_s=plan.total_timeout_s
                            )
                        except InvokeFailure as failure:
                            await _abort_with(
                                context,
                                failure.http_status,
                                fail(
                                    failure.code.value,
                                    failure.message,
                                    error=failure.message,
                                ),
                            )
                        async for frame in _frames(_emit(result_events(outcome))):
                            yield frame
                        return
                    async for frame in _frames(events):
                        yield frame
            finally:
                reset_trace_id(trace_token)
        finally:
            reset_upstream_remaining(token)


__all__ = ["InvokeServicer"]
