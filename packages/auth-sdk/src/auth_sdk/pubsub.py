"""Resilient single-channel Redis pub/sub listener shared by service loops.

Subscribes to one channel, drives `on_message` for every notification, and
reconnects with exponential backoff when subscribing or listening fails.
Callers keep their own logging and domain behavior through the optional hooks:
`on_subscribed` runs after each successful (re)subscribe (catch-up reloads,
connection logs) and `on_interrupted` reports the failure plus the delay before
the next attempt. Cancellation (process shutdown) exits without swallowing.
"""
from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from typing import Any

_BASE_DELAY_S = 1.0
_MAX_DELAY_S = 30.0


async def run_pubsub_listener(
    client_provider: Callable[[], Awaitable[Any]],
    channel: str,
    on_message: Callable[[Any], Awaitable[Any]],
    *,
    on_subscribed: Callable[[], Awaitable[Any]] | None = None,
    on_interrupted: Callable[[Exception, float], Awaitable[Any]] | None = None,
    base_delay: float = _BASE_DELAY_S,
    max_delay: float = _MAX_DELAY_S,
) -> None:
    """Run until cancelled: subscribe → on_subscribed → consume → reconnect.

    `client_provider` is a zero-arg async callable returning a redis client
    (None -> treated as an interruption and retried). `on_interrupted` receives
    the exception and the backoff delay that precedes the next attempt; the
    delay doubles per consecutive failure and resets on every subscribe.
    """
    delay = base_delay
    while True:
        pubsub = None
        try:
            client = await client_provider()
            if client is None:
                raise RuntimeError("no redis client configured")
            pubsub = client.pubsub(ignore_subscribe_messages=True)
            await pubsub.subscribe(channel)
            delay = base_delay
            if on_subscribed is not None:
                await on_subscribed()
            async for message in pubsub.listen():
                await on_message(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if on_interrupted is not None:
                await on_interrupted(exc, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, max_delay)
        finally:
            if pubsub is not None:
                with contextlib.suppress(Exception):
                    await pubsub.aclose()
