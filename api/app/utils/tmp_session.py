import json
import logging

import redis.asyncio as redis

from app.aioRedis import get_redis_connection

logger = logging.getLogger(__name__)

DEFAULT_TTL = 3600


class ChatSessionCache:
    """Cache user-AI conversation history in Redis with TTL-based expiry.

    Usage::

        cache = ChatSessionCache(session_id="user_123")
        await cache.append("user", "Hello")
        await cache.append("assistant", "Hi there!")
        history = await cache.get_history()
    """

    def __init__(self, session_id: str, ttl: int = DEFAULT_TTL):
        self.session_id = session_id
        self.ttl = ttl
        self._key = f"chat:session:{session_id}"

    @staticmethod
    async def _client() -> redis.StrictRedis:
        return await get_redis_connection()

    async def append(self, role: str, content: str) -> None:
        r = await self._client()
        entry = json.dumps({"role": role, "content": content}, ensure_ascii=False)
        await r.rpush(self._key, entry)
        await r.expire(self._key, self.ttl)

    async def append_many(self, messages: list[dict[str, str]]) -> None:
        """Batch append messages. Each dict should have ``role`` and ``content`` keys."""
        if not messages:
            return
        r = await self._client()
        entries = [
            json.dumps(m, ensure_ascii=False)
            for m in messages
            if "role" in m and "content" in m
        ]
        if entries:
            await r.rpush(self._key, *entries)
            await r.expire(self._key, self.ttl)

    async def get_history(self) -> list[dict[str, str]]:
        r = await self._client()
        raw = await r.lrange(self._key, 0, -1)
        return [json.loads(item) for item in raw]

    async def get_history_text(self, user_label: str = "User", ai_label: str = "Assistant") -> str:
        """Return conversation as a formatted text block."""
        history = await self.get_history()
        lines = []
        for msg in history:
            role = msg.get("role", "")
            content = msg.get("content", "")
            label = user_label if role == "user" else ai_label if role == "assistant" else role
            lines.append(f"{label}: {content}")
        return "\n".join(lines)

    async def reset(self) -> None:
        """Delete the session from Redis."""
        r = await self._client()
        await r.delete(self._key)

    async def touch(self) -> None:
        """Refresh the TTL without modifying data."""
        r = await self._client()
        await r.expire(self._key, self.ttl)
