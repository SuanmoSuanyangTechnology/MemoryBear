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
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

from fastapi.responses import StreamingResponse

from ..config import current_settings

logger = logging.getLogger(__name__)

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
    pending: asyncio.Task | None = None
    idle_elapsed = 0.0
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(iterator.__anext__())
            done, _ = await asyncio.wait({pending}, timeout=heartbeat_interval)
            if not done:
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
            try:
                chunk = pending.result()
            except StopAsyncIteration:
                return
            pending = None
            idle_elapsed = 0.0
            yield chunk
    finally:
        if pending is not None:
            if not pending.done():
                pending.cancel()
            with contextlib.suppress(BaseException):
                await pending
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
