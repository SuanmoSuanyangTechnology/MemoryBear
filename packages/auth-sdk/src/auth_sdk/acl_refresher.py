"""Hot-reloadable ACL rules holder for gateway-mode consumers.

Keeps an in-memory AclMatcher in sync with the identity-published Redis blob
(`acl:rules`) by subscribing to the change notification channel. The identity
service publishes `acl:rules:changed` after every successful full push; this
refresher treats every notification — and every successful (re)subscribe, to
cover notifications missed while disconnected — as a trigger for a full reload.

Fail-safe in both stages:
- initial load (ensure_loaded): unreachable/missing/corrupt -> the deny-all
  matcher stays in place;
- runtime reload (notification or reconnect): read/parse failures keep the
  last-known-good matcher. A valid empty rule list is NOT a failure — it is a
  deliberate deny-all state and replaces the current matcher.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from auth_sdk.acl import AclMatcher, parse_rules
from auth_sdk.pubsub import run_pubsub_listener

logger = logging.getLogger(__name__)

ACL_RULES_KEY = "acl:rules"
ACL_RULES_CHANGED_CHANNEL = "acl:rules:changed"

# Consumer redis clients carry no socket timeout; bound every provider call and
# GET so a hung connection cannot stall the reload path indefinitely.
_REFRESH_TIMEOUT_S = 2.0
_RECONNECT_BASE_DELAY_S = 1.0
_RECONNECT_MAX_DELAY_S = 30.0


class AclRefresher:
    """Hot-reloadable ACL holder (structurally satisfies AclEvaluator).

    `redis_provider` is a zero-arg async callable returning the redis client
    (or None when not configured). Lifecycle: the host lifespan awaits
    `start()` (initial load + subscription task) and `aclose()` (task stop);
    `ensure_loaded()` is also awaited before the interceptor is assembled so a
    host that never started the refresher keeps the plain load-once behavior.
    """

    def __init__(
        self,
        redis_provider: Callable[[], Awaitable[Any]],
        *,
        key: str = ACL_RULES_KEY,
        channel: str = ACL_RULES_CHANGED_CHANNEL,
    ) -> None:
        self._redis_provider = redis_provider
        self._key = key
        self._channel = channel
        self._matcher = AclMatcher([])  # fail-safe until the first successful load
        self._loaded = False
        self._load_lock = asyncio.Lock()
        self._started = False
        self._task: asyncio.Task[None] | None = None

    def allowed(self, caller: str, target: str, endpoint: str) -> bool:
        # Single attribute read: refresh swaps the matcher reference atomically.
        return self._matcher.allowed(caller, target, endpoint)

    async def ensure_loaded(self) -> None:
        """One-shot initial load; concurrent callers wait for the same attempt."""
        if self._loaded:
            return
        async with self._load_lock:
            if self._loaded:
                return
            await self.refresh()
            self._loaded = True

    async def refresh(self) -> bool:
        """Fetch, parse and atomically swap the matcher. True = replaced.

        Never raises except cancellation; every failure keeps the current
        matcher (deny-all before the first successful load, then last-known-good).
        """
        try:
            client = await asyncio.wait_for(self._redis_provider(), _REFRESH_TIMEOUT_S)
            if client is None:
                logger.warning("acl rules refresh skipped: no redis client configured")
                return False
            blob = await asyncio.wait_for(client.get(self._key), _REFRESH_TIMEOUT_S)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("acl rules refresh failed; keeping current rules", exc_info=True)
            return False
        if blob is None:
            logger.warning("acl rules key %r missing; keeping current rules", self._key)
            return False
        matcher = parse_rules(blob)
        if matcher is None:
            logger.warning("acl rules payload corrupt; keeping current rules")
            return False
        self._matcher = matcher
        logger.info("acl rules reloaded: %d rule(s)", matcher.rule_count)
        return True

    async def start(self) -> None:
        """Load rules once, then start the change-subscription task (idempotent)."""
        if self._started:
            return
        self._started = True
        try:
            await self.ensure_loaded()
        except asyncio.CancelledError:
            # A cancelled startup must not leave the guard set, or a later
            # start() would return early and the subscription never runs.
            self._started = False
            raise
        self._task = asyncio.create_task(self._run(), name="acl-rules-subscription")

    async def aclose(self) -> None:
        """Stop the subscription task (idempotent; safe before start)."""
        task, self._task = self._task, None
        self._started = False
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _run(self) -> None:
        """Resilient subscription loop over the change channel."""

        async def _on_subscribed() -> None:
            logger.info(
                "acl rules change subscription established: channel=%s", self._channel
            )
            # Catch up on changes missed while disconnected: a fresh subscribe
            # cannot assume the in-memory rules are current.
            await self.refresh()

        async def _on_interrupted(exc: Exception, delay: float) -> None:
            logger.warning(
                "acl rules subscription interrupted, reconnecting in %.1fs: %s", delay, exc
            )

        async def _on_message(_message: Any) -> None:
            # Any notification triggers a full reload; the payload is only a hint.
            await self.refresh()

        await run_pubsub_listener(
            self._redis_provider,
            self._channel,
            _on_message,
            on_subscribed=_on_subscribed,
            on_interrupted=_on_interrupted,
            base_delay=_RECONNECT_BASE_DELAY_S,
            max_delay=_RECONNECT_MAX_DELAY_S,
        )
