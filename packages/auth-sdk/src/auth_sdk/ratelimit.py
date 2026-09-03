"""API key 限流（语义/键名对齐 core api_key_service.RateLimiterService：QPS + 每日配额）。

Redis 故障时抛 SnapshotUnavailable（fail-closed），由网关转 401——限流不可判定即拒绝，
不降级放行。限流计数为 best-effort（INCR 先写后判，极低概率并发轻微超限，与老单体一致）。
"""
from datetime import datetime, timedelta, timezone

from redis.exceptions import RedisError

from auth_sdk.snapshot import SnapshotUnavailable


class ApiKeyRateLimiter:
    def __init__(self, redis):
        self._redis = redis

    async def check_qps(self, api_key_id: str, limit: int) -> tuple[bool, dict]:
        """QPS 限流：窗口 1s（键 rate_limit:qps:{id}，1 秒过期）。返回 (allowed, headers)。"""
        key = f"rate_limit:qps:{api_key_id}"
        try:
            async with self._redis.pipeline() as pipe:
                pipe.incr(key)
                pipe.expire(key, 1, nx=True)
                results = await pipe.execute()
        except RedisError as exc:
            raise SnapshotUnavailable(f"rate limit unavailable: {exc}") from exc
        current = results[0]
        now = datetime.now(timezone.utc)
        return current <= limit, {
            "X-RateLimit-Limit": str(limit),
            "X-RateLimit-Remaining": str(max(0, limit - current)),
            "X-RateLimit-Reset": str(int(now.timestamp()) + 1),
        }

    async def check_daily_requests(self, api_key_id: str, limit: int) -> tuple[bool, dict]:
        """每日配额：键 rate_limit:daily:{id}:{YYYYMMDD}，当天结束过期。返回 (allowed, headers)。"""
        now = datetime.now(timezone.utc)
        today = now.strftime("%Y%m%d")
        key = f"rate_limit:daily:{api_key_id}:{today}"
        tomorrow_0 = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        expire_seconds = int((tomorrow_0 - now).total_seconds())
        reset_ts = int(tomorrow_0.timestamp())
        try:
            async with self._redis.pipeline() as pipe:
                pipe.incr(key)
                pipe.expire(key, expire_seconds, nx=True)
                results = await pipe.execute()
        except RedisError as exc:
            raise SnapshotUnavailable(f"rate limit unavailable: {exc}") from exc
        current = results[0]
        return current <= limit, {
            "X-RateLimit-Limit": str(limit),
            "X-RateLimit-Remaining": str(max(0, limit - current)),
            "X-RateLimit-Reset": str(reset_ts),
        }
