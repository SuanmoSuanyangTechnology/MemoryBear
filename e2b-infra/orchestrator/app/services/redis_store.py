"""Redis state store for orchestrator — sandbox & run registry."""
from __future__ import annotations

import json
import logging
from typing import Any

import redis.asyncio as aioredis

from app.config import Settings

logger = logging.getLogger(__name__)

# Key prefixes (all under agent_runtime: namespace, db=1 shared with API)
POOL_AVAILABLE_KEY = "agent_runtime:host:{host_id}:pool:available"
POOL_TOTAL_KEY = "agent_runtime:host:{host_id}:pool:total"
POOL_LOCK_KEY = "agent_runtime:host:{host_id}:pool:lock"
RUN_KEY = "agent_runtime:run:{run_id}"
ACTIVE_RUNS_KEY = "agent_runtime:active_runs"
INSTANCE_KEY = "agent_runtime:orchestrator:{instance_id}"
INSTANCES_SET = "agent_runtime:orchestrators:active"
SANDBOX_KEY = "agent_runtime:sandbox:{sandbox_id}"


class RedisStore:
    def __init__(self, settings: Settings, instance_id: str):
        self._settings = settings
        self._instance_id = instance_id
        self._redis: aioredis.Redis | None = None

    async def connect(self) -> None:
        self._redis = aioredis.Redis(
            host=self._settings.REDIS_HOST,
            port=self._settings.REDIS_PORT,
            db=self._settings.REDIS_DB,
            password=self._settings.REDIS_PASSWORD or None,
            decode_responses=True,
            max_connections=20,
        )
        await self._redis.ping()
        logger.info("Redis connected db=%d", self._settings.REDIS_DB)

    async def disconnect(self) -> None:
        if self._redis:
            await self._redis.aclose()
            self._redis = None

    @property
    def client(self) -> aioredis.Redis:
        if self._redis is None:
            raise RuntimeError("Redis not connected")
        return self._redis

    # ── Instance heartbeat ──

    async def heartbeat(self) -> None:
        await self.client.set(INSTANCE_KEY.format(instance_id=self._instance_id), "1", ex=60)
        await self.client.sadd(INSTANCES_SET, self._instance_id)

    async def deregister(self) -> None:
        await self.client.delete(INSTANCE_KEY.format(instance_id=self._instance_id))
        await self.client.srem(INSTANCES_SET, self._instance_id)

    async def get_active_instances(self) -> set[str]:
        return await self.client.smembers(INSTANCES_SET)

    # ── Warm pool (per-host) ──

    def _pool_available_key(self, host_id: str) -> str:
        return POOL_AVAILABLE_KEY.format(host_id=host_id)

    def _pool_total_key(self, host_id: str) -> str:
        return POOL_TOTAL_KEY.format(host_id=host_id)

    def _pool_lock_key(self, host_id: str) -> str:
        return POOL_LOCK_KEY.format(host_id=host_id)

    async def pool_push(self, host_id: str, container_id: str) -> None:
        await self.client.lpush(self._pool_available_key(host_id), container_id)
        await self.client.incr(self._pool_total_key(host_id))

    async def pool_pop(self, host_id: str) -> str | None:
        return await self.client.rpop(self._pool_available_key(host_id))

    async def pool_available_count(self, host_id: str) -> int:
        return await self.client.llen(self._pool_available_key(host_id))

    async def pool_total(self, host_id: str) -> int:
        val = await self.client.get(self._pool_total_key(host_id))
        return int(val) if val else 0

    async def pool_decr_total(self, host_id: str) -> int:
        return await self.client.decr(self._pool_total_key(host_id))

    async def pool_incr_total(self, host_id: str) -> int:
        return await self.client.incr(self._pool_total_key(host_id))

    async def pool_acquire_create_lock(self, host_id: str) -> bool:
        return await self.client.set(
            self._pool_lock_key(host_id), self._instance_id, ex=10, nx=True
        )

    async def pool_release_create_lock(self, host_id: str) -> None:
        await self.client.delete(self._pool_lock_key(host_id))

    async def pool_clear(self, host_id: str) -> None:
        await self.client.delete(
            self._pool_available_key(host_id),
            self._pool_total_key(host_id),
            self._pool_lock_key(host_id),
        )

    # ── Sandbox registry ──

    async def save_sandbox(self, sandbox_id: str, data: dict[str, Any]) -> None:
        key = SANDBOX_KEY.format(sandbox_id=sandbox_id)
        await self.client.set(key, json.dumps(data), ex=3600)

    async def get_sandbox(self, sandbox_id: str) -> dict[str, Any] | None:
        key = SANDBOX_KEY.format(sandbox_id=sandbox_id)
        val = await self.client.get(key)
        return json.loads(val) if val else None

    async def delete_sandbox(self, sandbox_id: str) -> None:
        await self.client.delete(SANDBOX_KEY.format(sandbox_id=sandbox_id))

    # ── Run registry ──

    async def save_run(self, run_id: str, data: dict[str, Any]) -> None:
        key = RUN_KEY.format(run_id=run_id)
        async with self.client.pipeline() as pipe:
            pipe.set(key, json.dumps(data), ex=3600)
            pipe.sadd(ACTIVE_RUNS_KEY, run_id)
            await pipe.execute()

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        val = await self.client.get(RUN_KEY.format(run_id=run_id))
        return json.loads(val) if val else None

    async def delete_run(self, run_id: str) -> None:
        async with self.client.pipeline() as pipe:
            pipe.delete(RUN_KEY.format(run_id=run_id))
            pipe.srem(ACTIVE_RUNS_KEY, run_id)
            await pipe.execute()

    async def get_active_runs(self) -> set[str]:
        return await self.client.smembers(ACTIVE_RUNS_KEY)

    async def update_run_status(self, run_id: str, status: str) -> None:
        existing = await self.get_run(run_id)
        if existing:
            existing["status"] = status
            await self.client.set(RUN_KEY.format(run_id=run_id), json.dumps(existing), keepttl=True)

    async def flush_orchestrator_keys(self) -> None:
        """Remove all agent_runtime:* keys from Redis (full cleanup)."""
        keys = []
        async for key in self.client.scan_iter(match="agent_runtime:*"):
            keys.append(key)
        if keys:
            await self.client.delete(*keys)
        logger.info("Flushed %d orchestrator keys", len(keys))
