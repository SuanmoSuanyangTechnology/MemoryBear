"""Redis idempotency key reservations for publish/install.

The store is sync because the management plane is sync. Values are JSON:
``{"fingerprint": <sha256>, "state": "in_progress"|"done", "response": {...}}``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Literal

TTL_SECONDS = 24 * 60 * 60

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IdempotencyReservation:
    state: Literal["new", "replay", "in_progress", "mismatch"]
    response: dict[str, Any] | None = None


class IdempotencyStore:
    def __init__(self, redis: Any, ttl_seconds: int = TTL_SECONDS) -> None:
        self.redis = redis
        self.ttl_seconds = ttl_seconds

    @staticmethod
    def key(operation: str, tenant_id: str, actor_id: str, key: str) -> str:
        return f"idem:external-catalog:{operation}:{tenant_id}:{actor_id}:{key}"

    def begin(
        self,
        operation: str,
        tenant_id: str,
        actor_id: str,
        key: str,
        fingerprint: str,
    ) -> IdempotencyReservation:
        redis_key = self.key(operation, tenant_id, actor_id, key)
        initial = json.dumps({"fingerprint": fingerprint, "state": "in_progress"})
        if self.redis.set(redis_key, initial, nx=True, ex=self.ttl_seconds):
            return IdempotencyReservation("new")
        raw = self.redis.get(redis_key)
        if raw is None:
            if self.redis.set(redis_key, initial, nx=True, ex=self.ttl_seconds):
                return IdempotencyReservation("new")
            raw = self.redis.get(redis_key)
        stored = self._decode(raw)
        if stored is None:
            # The reservation vanished or is unreadable; assume it is still
            # in flight rather than starting a duplicate operation.
            return IdempotencyReservation("in_progress")
        if stored.get("fingerprint") != fingerprint:
            return IdempotencyReservation("mismatch")
        if stored.get("state") == "done":
            return IdempotencyReservation("replay", stored.get("response"))
        return IdempotencyReservation("in_progress")

    def complete(
        self,
        operation: str,
        tenant_id: str,
        actor_id: str,
        key: str,
        fingerprint: str,
        response: dict[str, Any],
    ) -> None:
        redis_key = self.key(operation, tenant_id, actor_id, key)
        self.redis.set(
            redis_key,
            json.dumps(
                {"fingerprint": fingerprint, "state": "done", "response": response}
            ),
            ex=self.ttl_seconds,
        )

    def abort(
        self,
        operation: str,
        tenant_id: str,
        actor_id: str,
        key: str,
        fingerprint: str,
    ) -> None:
        redis_key = self.key(operation, tenant_id, actor_id, key)
        stored = self._decode(self.redis.get(redis_key))
        if stored is None:
            return
        if stored.get("fingerprint") == fingerprint:
            self.redis.delete(redis_key)

    @staticmethod
    def _decode(raw: Any) -> dict[str, Any] | None:
        if not raw:
            return None
        try:
            stored = json.loads(raw)
        except (TypeError, ValueError):
            logger.warning("ignoring malformed idempotency record")
            return None
        return stored if isinstance(stored, dict) else None
