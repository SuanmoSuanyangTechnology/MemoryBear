"""Cache invalidation and Redis-client resource locks for migrated operations."""

from contextlib import AbstractContextManager


class RedisTaskCache:
    def __init__(self, redis) -> None:
        self.redis = redis

    def lock(self, key: str) -> AbstractContextManager[object]:
        # Best-effort exclusion window, not an execution deadline (threads ignore Celery limits).
        return self.redis.client().lock(key + ":lock", timeout=1800, blocking=False)

    def invalidate(self, user_id: str, workspace_id: str) -> None:
        client = self.redis.client()
        for pattern in (
            f"cache:quota_breakdown:{user_id}:*",
            f"cache:forget_candidates:{user_id}:*",
            f"cache:memory_count:{workspace_id}:*",
            f"cache:memory_count_total:{workspace_id}:*",
        ):
            batch = []
            for key in client.scan_iter(match=pattern, count=500):
                batch.append(key)
                if len(batch) == 500:
                    client.delete(*batch)
                    batch.clear()
            if batch:
                client.delete(*batch)
