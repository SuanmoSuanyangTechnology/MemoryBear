"""固定窗口限流（评审稿 4.2.2）：INCR + EXPIRE 原子窗口，key 由调用方决定计数身份。"""
from __future__ import annotations


class FixedWindowRateLimiter:
    def __init__(self, redis) -> None:
        self._redis = redis

    async def check(self, key: str, limit: int, window_seconds: int = 60) -> tuple[bool, dict]:
        count = await self._redis.incr(f"gateway:rate_limit:{key}")
        if count == 1:
            await self._redis.expire(f"gateway:rate_limit:{key}", window_seconds)
        remaining = max(0, limit - count)
        headers = {
            "X-RateLimit-Limit": str(limit),
            "X-RateLimit-Remaining": str(remaining),
        }
        if count > limit:
            headers["Retry-After"] = str(window_seconds)
            return False, headers
        return True, headers
