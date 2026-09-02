import asyncio
from typing import Any, Protocol
from uuid import uuid4

REDIS_MIGRATION_LOCK_PREFIX = "redbear:memory:es:index-migration"
DEFAULT_LOCK_TTL_MS = 30_000
DEFAULT_RENEW_INTERVAL_SECONDS = 10.0

RENEW_LOCK_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("pexpire", KEYS[1], ARGV[2])
end
return 0
"""

RELEASE_LOCK_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
end
return 0
"""

CHECK_LOCK_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return 1
end
return 0
"""


class AsyncRedisClient(Protocol):
    async def set(
        self,
        name: str,
        value: str,
        *,
        nx: bool = False,
        px: int | None = None,
    ) -> Any: ...

    async def get(self, name: str) -> Any: ...

    async def eval(
        self,
        script: str,
        numkeys: int,
        *keys_and_args: Any,
    ) -> Any: ...


class RedisMigrationLease:
    """A renewable token-owned Redis lease for one index alias."""

    def __init__(
        self,
        redis_client: AsyncRedisClient,
        alias: str,
        *,
        ttl_ms: int = DEFAULT_LOCK_TTL_MS,
        renew_interval_seconds: float = DEFAULT_RENEW_INTERVAL_SECONDS,
    ) -> None:
        if ttl_ms <= 0:
            raise ValueError("Redis migration lock ttl_ms must be positive")
        if renew_interval_seconds <= 0:
            raise ValueError(
                "Redis migration lock renew interval must be positive"
            )
        if renew_interval_seconds * 1000 >= ttl_ms:
            raise ValueError(
                "Redis migration lock renew interval must be shorter than TTL"
            )

        self.redis = redis_client
        self.alias = alias
        self.key = f"{REDIS_MIGRATION_LOCK_PREFIX}:{alias}"
        self.token = uuid4().hex
        self.ttl_ms = ttl_ms
        self.renew_interval_seconds = renew_interval_seconds
        self._acquired = False
        self._renew_task: asyncio.Task[None] | None = None
        self._renew_error: BaseException | None = None

    @property
    def acquired(self) -> bool:
        return self._acquired

    async def acquire(self) -> bool:
        if self._acquired:
            return True
        acquired = await self.redis.set(
            self.key,
            self.token,
            nx=True,
            px=self.ttl_ms,
        )
        if not acquired:
            return False
        self._acquired = True
        self._renew_error = None
        self._renew_task = asyncio.create_task(
            self._renew_loop(),
            name=f"redis-es-index-lock-renew:{self.alias}",
        )
        return True

    async def _renew_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.renew_interval_seconds)
                await self.renew_now()
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            self._renew_error = exc

    async def renew_now(self) -> None:
        if not self._acquired:
            raise RuntimeError(
                f"Redis migration lock is not held: {self.key}"
            )
        if self._renew_error is not None:
            raise RuntimeError(
                f"Redis migration lock renewal failed: {self.key}"
            ) from self._renew_error
        renewed = await self.redis.eval(
            RENEW_LOCK_SCRIPT,
            1,
            self.key,
            self.token,
            self.ttl_ms,
        )
        if renewed != 1:
            error = RuntimeError(
                f"Redis migration lock ownership lost: {self.key}"
            )
            self._renew_error = error
            raise error

    async def ensure_owned(self) -> None:
        if not self._acquired:
            raise RuntimeError(
                f"Redis migration lock is not held: {self.key}"
            )
        if self._renew_error is not None:
            raise RuntimeError(
                f"Redis migration lock renewal failed: {self.key}"
            ) from self._renew_error
        owned = await self.redis.eval(
            CHECK_LOCK_SCRIPT,
            1,
            self.key,
            self.token,
        )
        if owned != 1:
            raise RuntimeError(
                f"Redis migration lock ownership lost: {self.key}"
            )

    async def release(self) -> bool:
        if not self._acquired:
            return False
        renew_task = self._renew_task
        if renew_task is not None:
            renew_task.cancel()
            try:
                await renew_task
            except asyncio.CancelledError:
                pass
        self._renew_task = None

        released = await self.redis.eval(
            RELEASE_LOCK_SCRIPT,
            1,
            self.key,
            self.token,
        )
        self._acquired = False
        return released == 1
