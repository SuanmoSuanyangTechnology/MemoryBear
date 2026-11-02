"""Invoke event model shared by the HTTP SSE endpoint and the gRPC servicer.

The service-side contract is a sequence of typed events (result / chunk /
usage / error / done). HTTP renders them as SSE lines; gRPC renders them as
``InvokeFrame`` messages. One event source keeps the two faces from drifting.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass
from typing import Any

from ..errors import BizCode
from .invoke_service import InvokeFailure, InvokeOutcome

logger = logging.getLogger(__name__)

EVENT_RESULT = "result"
EVENT_CHUNK = "chunk"
EVENT_USAGE = "usage"
EVENT_ERROR = "error"
EVENT_DONE = "done"


@dataclass(frozen=True)
class InvokeEvent:
    kind: str
    payload: dict[str, Any]


def error_payload(failure: InvokeFailure) -> dict[str, Any]:
    """HTTP error-frame body: code name plus attribution and retry hint."""
    return {
        "code": failure.code.name if isinstance(failure.code, BizCode) else str(failure.code),
        "message": failure.message,
        "attempts": failure.attempts,
        "channel_id": None if failure.channel_id is None else str(failure.channel_id),
        "retryable": failure.retryable,
    }


def error_info(failure: InvokeFailure) -> dict[str, Any]:
    """gRPC ``ErrorInfo`` subset: the proto cannot express attempts/channel_id."""
    return {
        "code": failure.code.name if isinstance(failure.code, BizCode) else str(failure.code),
        "message": failure.message,
        "retryable": failure.retryable,
    }


def failure_events(failure: InvokeFailure) -> list[InvokeEvent]:
    events: list[InvokeEvent] = []
    if failure.usage_event is not None:
        # Count only calls that actually reached a provider.
        events.append(InvokeEvent(EVENT_USAGE, failure.usage_event.to_stream_dict()))
    events.append(InvokeEvent(EVENT_ERROR, error_payload(failure)))
    return events


def result_events(outcome: InvokeOutcome) -> list[InvokeEvent]:
    return [
        InvokeEvent(EVENT_RESULT, {"data": outcome.data}),
        InvokeEvent(EVENT_USAGE, outcome.usage_event.to_stream_dict()),
        InvokeEvent(EVENT_DONE, {"status": outcome.status.value}),
    ]


def chunk_done_events(outcome: InvokeOutcome) -> list[InvokeEvent]:
    """Streamed llm close-out: usage then done, never a result frame."""
    return [
        InvokeEvent(EVENT_USAGE, outcome.usage_event.to_stream_dict()),
        InvokeEvent(EVENT_DONE, {"status": outcome.status.value}),
    ]


def internal_error_event() -> InvokeEvent:
    """Escape hatch for facade contract breaches: a terminal error beats silence."""
    return InvokeEvent(
        EVENT_ERROR,
        {
            "code": BizCode.INTERNAL_ERROR.name,
            "message": "模型调用内部错误",
            "attempts": 0,
            "channel_id": None,
            "retryable": True,
        },
    )


async def stream_events(
    task: asyncio.Task[InvokeOutcome],
) -> AsyncGenerator[InvokeEvent, None]:
    """Non-chunk frame order; the upstream call runs in its own task.

    On client disconnect the ASGI layer closes this generator: the task is
    cancelled and awaited so provider connections and concurrency slots are
    released. During ``GeneratorExit`` the generator must not suspend, hence
    the task is only cancelled (never awaited) on that path.
    """
    closing = False
    try:
        try:
            outcome = await task
        except InvokeFailure as failure:
            for event in failure_events(failure):
                yield event
            return
        for event in result_events(outcome):
            yield event
    except GeneratorExit:
        closing = True
        raise
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("invoke stream crashed: %s", type(exc).__name__)
        yield internal_error_event()
    finally:
        if not task.done():
            task.cancel()
        if not closing:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task


async def chunk_events(
    task: asyncio.Task[InvokeOutcome],
    sink: asyncio.Queue[dict[str, Any]],
) -> AsyncGenerator[InvokeEvent, None]:
    """Streamed llm order: chunk* then usage then done.

    The facade pushes increments into a bounded queue (``await put`` applies
    back-pressure). This layer races the queue against the task: a ready chunk
    ships immediately (real-time), and once the task finishes the buffered
    chunks are drained before close-out. After the task finishes no further
    ``put`` can happen, so ``get_nowait`` is safe (a sentinel would need an
    await in ``finally`` and deadlock the route when the queue is full).
    """
    closing = False
    seq = 0
    try:
        try:
            while True:
                getter: asyncio.Task[dict[str, Any]] = asyncio.ensure_future(sink.get())
                try:
                    done, _ = await asyncio.wait(
                        {getter, task}, return_when=asyncio.FIRST_COMPLETED
                    )
                except BaseException:
                    getter.cancel()
                    raise
                if getter in done:
                    yield InvokeEvent(
                        EVENT_CHUNK, {"message": getter.result(), "seq": seq}
                    )
                    seq += 1
                    continue
                getter.cancel()
                while True:
                    try:
                        payload = sink.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    yield InvokeEvent(EVENT_CHUNK, {"message": payload, "seq": seq})
                    seq += 1
                outcome = await task
                for event in chunk_done_events(outcome):
                    yield event
                return
        except InvokeFailure as failure:
            for event in failure_events(failure):
                yield event
        except GeneratorExit:
            closing = True
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("invoke chunk stream crashed: %s", type(exc).__name__)
            yield internal_error_event()
    finally:
        if not task.done():
            task.cancel()
        if not closing:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task


def _frame(event: str, payload: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def sse_frames(events: AsyncGenerator[InvokeEvent, None]) -> AsyncIterator[str]:
    """Render typed events as SSE lines (byte-identical to the pre-split route).

    Rendering sits outside the event-source guard, so a non-JSON payload (a
    facade contract breach) is translated to the terminal error frame here
    instead of escaping as a truncated stream.
    """
    try:
        async for event in events:
            try:
                yield _frame(event.kind, event.payload)
            except Exception as exc:
                logger.exception("invoke frame render crashed: %s", type(exc).__name__)
                yield _frame(EVENT_ERROR, internal_error_event().payload)
                break
    finally:
        await events.aclose()


__all__ = [
    "EVENT_CHUNK",
    "EVENT_DONE",
    "EVENT_ERROR",
    "EVENT_RESULT",
    "EVENT_USAGE",
    "InvokeEvent",
    "chunk_done_events",
    "chunk_events",
    "error_info",
    "error_payload",
    "failure_events",
    "internal_error_event",
    "result_events",
    "sse_frames",
    "stream_events",
]
