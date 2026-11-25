"""Redis reservations that make a write endpoint safe to retry.

Same contract as ``application-service``'s external-catalog store: the key is
reserved with ``SET NX`` before the work starts, a repeat replays the stored
response, and a different body under the same key is a conflict. The record is
JSON — ``{"fingerprint": <sha256>, "state": "in_progress"|"done", "response": {}}``
— and profile is async, so this uses ``redis.asyncio``.

Failure policy is best-effort, like the re-embed dispatch: if Redis is
unavailable the caller proceeds without a reservation (returned as ``new`` and
reported by the caller's log), because the DB row is the source of truth and a
lost reservation only costs a possible duplicate rebuild, never a lost job.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any, Literal

logger = logging.getLogger(__name__)

#: Window for a caller-supplied key: a retried registration must replay here.
DEFAULT_TTL_SECONDS = 24 * 60 * 60
#: Window for a derived key. Short on purpose: it only has to cover transport
#: retries, while a later legitimate switch to the same model pair must not be
#: mistaken for a replay of the earlier one.
DERIVED_TTL_SECONDS = 60

MIN_KEY_LENGTH = 8
MAX_KEY_LENGTH = 128

ReservationState = Literal["new", "replay", "in_progress", "mismatch"]


@dataclass(frozen=True, slots=True)
class IdempotencyReservation:
    state: ReservationState
    response: dict[str, Any] | None = None


def request_fingerprint(body: dict[str, Any]) -> str:
    """Stable hash of a request body (sorted keys, no whitespace)."""

    encoded = json.dumps(
        body,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def derived_key(fingerprint: str) -> str:
    """A key for callers that send none: stable per request body."""

    return "auto:" + hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:32]


class IdempotencyStore:
    """Reserve, replay, complete and release one operation's keys.

    ``redis_manager`` may be ``None`` (a unit-test fake runtime); every method is
    then a no-op that reports ``new``, so the caller keeps working unguarded.
    """

    def __init__(self, redis_manager: Any, *, operation: str) -> None:
        self._redis_manager = redis_manager
        self._operation = operation

    def _key(self, scope: str, key: str) -> str:
        return f"idem:memory-profile:{self._operation}:{scope}:{key}"

    async def begin(
        self,
        *,
        scope: str,
        key: str,
        fingerprint: str,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> IdempotencyReservation:
        if self._redis_manager is None:
            return IdempotencyReservation("new")
        client = await self._redis_manager.client()
        redis_key = self._key(scope, key)
        initial = json.dumps({"fingerprint": fingerprint, "state": "in_progress"})
        if await client.set(redis_key, initial, nx=True, ex=ttl_seconds):
            return IdempotencyReservation("new")
        raw = await client.get(redis_key)
        if raw is None:
            # The reservation expired between SET NX and GET; try to take it.
            if await client.set(redis_key, initial, nx=True, ex=ttl_seconds):
                return IdempotencyReservation("new")
            raw = await client.get(redis_key)
        stored = _decode(raw)
        if stored is None:
            # Unreadable record: assume the first attempt is still running
            # rather than starting a duplicate operation.
            return IdempotencyReservation("in_progress")
        if stored.get("fingerprint") != fingerprint:
            return IdempotencyReservation("mismatch")
        if stored.get("state") == "done":
            response = stored.get("response")
            return IdempotencyReservation(
                "replay", response if isinstance(response, dict) else None
            )
        return IdempotencyReservation("in_progress")

    async def complete(
        self,
        *,
        scope: str,
        key: str,
        fingerprint: str,
        response: dict[str, Any],
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        if self._redis_manager is None:
            return
        client = await self._redis_manager.client()
        await client.set(
            self._key(scope, key),
            json.dumps(
                {"fingerprint": fingerprint, "state": "done", "response": response}
            ),
            ex=ttl_seconds,
        )

    async def abort(
        self,
        *,
        scope: str,
        key: str,
        fingerprint: str,
    ) -> None:
        """Release a reservation this request owns; never touch another's."""

        if self._redis_manager is None:
            return
        client = await self._redis_manager.client()
        redis_key = self._key(scope, key)
        stored = _decode(await client.get(redis_key))
        if stored is None:
            return
        if stored.get("fingerprint") == fingerprint:
            await client.delete(redis_key)


def _decode(raw: Any) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        stored = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("ignoring malformed idempotency record")
        return None
    return stored if isinstance(stored, dict) else None


__all__ = [
    "DEFAULT_TTL_SECONDS",
    "DERIVED_TTL_SECONDS",
    "IdempotencyReservation",
    "IdempotencyStore",
    "MAX_KEY_LENGTH",
    "MIN_KEY_LENGTH",
    "ReservationState",
    "derived_key",
    "request_fingerprint",
]