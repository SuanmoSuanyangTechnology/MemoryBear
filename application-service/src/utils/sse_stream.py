"""SSE stream helpers: heartbeat keep-alive, idle cutoff, disconnect cancel.

Service-side timing discipline (validated at startup by
``check_sse_timeout_ordering``): the heartbeat interval stays strictly below
the idle timeout, and the idle timeout strictly below the gateway's 300s
idle watchdog — the gateway closes a silent upstream stream once that
watchdog fires (gateway forward.py). Heartbeats keep the connection active
across quiet gaps (model reasoning, long tool calls); the idle cutoff closes
a genuinely dead stream on the service side before the gateway would.

Client disconnects surface as ``GeneratorExit``/``CancelledError`` inside the
wrapper; the wrapper closes the source generator so in-flight upstream work
(model invoke streams) is cancelled instead of running to completion.

All source iterations run in one dedicated pump task. Awaiting each
``__anext__`` from a fresh task would run the generator body in a different
Context copy per chunk, so context variables bound across yields (usage
attribution, for example) would be set and reset in different contexts and
raise ``ValueError`` on teardown.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

from fastapi.responses import StreamingResponse

from ..config import current_settings

logger = logging.getLogger(__name__)

# Bounded hand-off queue: a fast source cannot outrun a slow client without
# limit, and the pump parks on put() to apply backpressure.
_QUEUE_MAXSIZE = 16

_ITEM = "item"
_ERROR = "error"
_END = "end"

# SSE comment frame: keeps the connection active without dispatching an event.
HEARTBEAT_FRAME = ": heartbeat\n\n"

# Response headers shared by every SSE endpoint (X-Accel-Buffering keeps the
# entry nginx from buffering; the gateway re-asserts it on its own hop).
SSE_RESPONSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}

# Final frame emitted when the source stays silent past the idle timeout.
IDLE_ERROR_FRAME = 'event: end\ndata: {"error": "stream idle timeout"}\n\n'


async def _pump_source(
        source: AsyncIterator[str],
        queue: asyncio.Queue,
) -> None:
    """Iterate the source in this task and forward frames into ``queue``.

    Keeping every ``__anext__`` in one task keeps the generator body inside a
    single context for its whole lifetime. The terminal frame is always
    delivered so the consumer can distinguish end-of-stream from silence.
    """
    try:
        async for chunk in source:
            await queue.put((_ITEM, chunk))
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - re-raised by the consumer
        await queue.put((_ERROR, exc))
    else:
        await queue.put((_END, None))
    finally:
        with contextlib.suppress(BaseException):
            await source.aclose()


async def with_sse_keepalive(
        source: AsyncIterator[str],
        *,
        idle_error_frame: str = IDLE_ERROR_FRAME,
) -> AsyncIterator[str]:
    """Wrap an SSE string stream with heartbeats and an idle cutoff.

    - While the source is silent, one heartbeat comment is emitted per
      heartbeat interval, keeping the gateway's idle watchdog from firing.
    - Silence beyond the idle timeout closes the stream with
      ``idle_error_frame``; the source is then closed, cancelling whatever
      upstream call is still in flight.
    - A generator close or task cancellation (client disconnect) skips the
      frame and unwinds straight into the source teardown.
    """
    settings = current_settings()
    heartbeat_interval = settings.application_service_sse_heartbeat_interval_seconds
    idle_timeout = settings.application_service_sse_idle_timeout_seconds

    iterator = source.__aiter__()
    queue: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
    pump = asyncio.create_task(_pump_source(iterator, queue))
    idle_elapsed = 0.0
    try:
        while True:
            try:
                kind, payload = await asyncio.wait_for(
                    queue.get(), timeout=heartbeat_interval)
            except TimeoutError:
                idle_elapsed += heartbeat_interval
                if idle_elapsed >= idle_timeout:
                    logger.warning(
                        "SSE stream idle for %.0fs (limit %.0fs), closing",
                        idle_elapsed, idle_timeout,
                    )
                    yield idle_error_frame
                    return
                yield HEARTBEAT_FRAME
                continue
            if kind == _ERROR:
                raise payload
            if kind == _END:
                return
            idle_elapsed = 0.0
            yield payload
    finally:
        if not pump.done():
            pump.cancel()
        with contextlib.suppress(BaseException):
            await pump
        # Safety net for a pump cancelled before it ever ran; once it starts,
        # the pump owns the source teardown.
        with contextlib.suppress(BaseException):
            await iterator.aclose()


def sse_response(
        source: AsyncIterator[str],
        *,
        idle_error_frame: str = IDLE_ERROR_FRAME,
) -> StreamingResponse:
    """Build a StreamingResponse over the keep-alive wrapper."""

    return StreamingResponse(
        with_sse_keepalive(source, idle_error_frame=idle_error_frame),
        media_type="text/event-stream",
        headers=dict(SSE_RESPONSE_HEADERS),
    )


__all__ = [
    "HEARTBEAT_FRAME",
    "IDLE_ERROR_FRAME",
    "SSE_RESPONSE_HEADERS",
    "sse_response",
    "with_sse_keepalive",
]
