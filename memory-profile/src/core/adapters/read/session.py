"""Redis conversation-history adapter for the streaming read.

Ported from the host's ``app/utils/tmp_session.py::ChatSessionCache``. The key,
TTL, value encoding and list operations are wire-compatible with the API host on
purpose: management-facing streaming reads and API-side conversation turns must
see the same history, which means memory-profile has to run against the *same*
Redis logical database (design §6.2 / risk R3).

Compatibility contract:

* key   ``chat:session:{session_id}``
* TTL   3600 seconds, refreshed on every append (never on read)
* value JSON list: one ``{"role": ..., "content": ...}`` object per entry,
  appended with ``RPUSH`` and read with ``LRANGE 0 -1`` (oldest first)

Cache failures are *not* swallowed here: the streaming use case decides how a
broken history degrades (the host's validation service lets the error reach the
SSE error event instead of answering with half an empty conversation).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - type-only imports
    from src.infrastructure.redis import RedisManager
    from src.runtime import ProfileRuntime

SESSION_KEY_PREFIX = "chat:session:"
SESSION_TTL_SECONDS = 3600


class RedisSessionHistory:
    """``SessionHistoryPort`` over the host's Redis chat-session cache."""

    def __init__(
        self,
        redis: RedisManager,
        *,
        ttl: int = SESSION_TTL_SECONDS,
    ) -> None:
        self._redis = redis
        self._ttl = ttl

    async def history(self, session_id: str) -> list[dict[str, str]]:
        """Return the stored turns, oldest first (empty when the key is absent)."""

        client = await self._redis.client()
        raw = await client.lrange(self._key(session_id), 0, -1)
        return [json.loads(item) for item in raw]

    async def append(
        self, session_id: str, messages: Sequence[Mapping[str, str]]
    ) -> None:
        """Append ``messages`` in order and refresh the session TTL.

        Entries without both ``role`` and ``content`` are dropped, exactly like
        the host's ``append_many``; an empty batch touches neither Redis nor the
        TTL.
        """

        entries = [
            json.dumps(dict(message), ensure_ascii=False)
            for message in messages
            if "role" in message and "content" in message
        ]
        if not entries:
            return
        client = await self._redis.client()
        key = self._key(session_id)
        await client.rpush(key, *entries)
        await client.expire(key, self._ttl)

    @staticmethod
    def _key(session_id: str) -> str:
        return f"{SESSION_KEY_PREFIX}{session_id}"


def build_session_history(runtime: ProfileRuntime) -> RedisSessionHistory:
    """Composition entry point of the :class:`SessionHistoryPort`.

    ``runtime`` is the process :class:`~src.runtime.ProfileRuntime`; only its
    ``redis`` manager is used, so a fake runtime with a ``redis`` attribute is
    enough in tests.
    """

    return RedisSessionHistory(runtime.redis)


__all__ = [
    "SESSION_KEY_PREFIX",
    "SESSION_TTL_SECONDS",
    "RedisSessionHistory",
    "build_session_history",
]