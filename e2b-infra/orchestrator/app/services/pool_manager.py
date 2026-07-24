"""Warm pool manager — per-host container pools backed by Redis."""
from __future__ import annotations

import asyncio
import logging

import docker
from docker.models.containers import Container

from app.config import Settings
from app.services.redis_store import RedisStore

logger = logging.getLogger(__name__)


class PoolManager:
    """Manages warm pools across multiple Docker hosts.

    Each host has its own Redis-backed pool:
      - agent_runtime:host:{host_id}:pool:available — LIST of container IDs
      - agent_runtime:host:{host_id}:pool:total — active container count
      - agent_runtime:host:{host_id}:pool:lock — creation lock (SET NX)

    Acquire: RPOP from list → docker.containers.get(id)
    Release: container.remove → DECR total
    Refill: background loop per host with SET NX lock
    """

    def __init__(self, settings: Settings, redis_store: RedisStore):
        self._settings = settings
        self._redis = redis_store
        self._hosts: list[str] = [
            h.strip() for h in settings.DOCKER_HOSTS.split(",") if h.strip()
        ]
        self._clients: dict[str, docker.DockerClient] = {}
        self._refill_tasks: dict[str, asyncio.Task] = {}
        self._running = False

    async def start(self) -> None:
        self._running = True
        for host in self._hosts:
            host_id = _host_id(host)
            self._clients[host_id] = docker.DockerClient(base_url=host)
            self._refill_tasks[host_id] = asyncio.create_task(self._refill_loop(host))
        logger.info("PoolManager started: hosts=%d pool_size=%d", len(self._hosts), self._settings.WARM_POOL_SIZE)

    async def stop(self) -> None:
        self._running = False
        for host_id, task in self._refill_tasks.items():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self._drain_pools()
        for host_id, client in self._clients.items():
            client.close()
        logger.info("PoolManager stopped")

    async def _drain_pools(self) -> None:
        """Remove all warm pool containers and clear Redis pool keys."""
        loop = asyncio.get_running_loop()
        for host_url in self._hosts:
            host_id = _host_id(host_url)
            client = self._clients.get(host_id)
            # Pop and destroy all available containers
            while True:
                container_id = await self._redis.pool_pop(host_id)
                if not container_id:
                    break
                if client:
                    try:
                        container = await loop.run_in_executor(
                            None, client.containers.get, container_id,
                        )
                        await loop.run_in_executor(
                            None, lambda: container.remove(force=True),
                        )
                        logger.info("Drained warm container %s", container_id[:12])
                    except Exception as exc:
                        logger.debug("Failed to drain container %s: %s", container_id[:12], exc)
            # Clear pool keys
            await self._redis.pool_clear(host_id)

    async def acquire(self, image: str = "") -> tuple[Container, str, bool]:
        """Get a container from any available host's warm pool.

        Returns (container, host_id, pool_hit).
        Falls back to cold-create on the least-loaded host.
        """
        # Round-robin through hosts trying warm pool
        for host in self._hosts:
            host_id = _host_id(host)
            container_id = await self._redis.pool_pop(host_id)
            if container_id:
                client = self._clients[host_id]
                try:
                    container = await asyncio.get_running_loop().run_in_executor(
                        None, client.containers.get, container_id,
                    )
                    logger.debug("Warm pool hit host=%s container=%s", host_id, container_id[:12])
                    return container, host_id, True
                except Exception:
                    logger.warning("Stale container in pool: %s", container_id[:12])
                    await self._redis.pool_decr_total(host_id)
                    continue  # try next

        # Pool miss — cold create on least-loaded host
        host = self._select_least_loaded_host()
        host_id = _host_id(host)
        container = await self._create_container(host, host_id)
        return container, host_id, False

    async def release(self, host_id: str) -> None:
        """Signal that a container from this host was destroyed (DECR total)."""
        await self._redis.pool_decr_total(host_id)

    def get_client(self, host_id: str) -> docker.DockerClient:
        return self._clients[host_id]

    def get_hosts(self) -> list[str]:
        return list(self._hosts)

    # ── Internal ──

    def _select_least_loaded_host(self) -> str:
        """Pick the Docker host with fewest running containers. Defaults to first."""
        return self._hosts[0]  # single-host default; multi-host load balancing TBD

    async def _create_container(self, host_url: str, host_id: str) -> Container:
        client = self._clients[host_id]
        image = self._settings.TEMPLATE_ID
        loop = asyncio.get_running_loop()

        import uuid
        name = f"agent-runner-{uuid.uuid4().hex[:12]}"

        # Pull latest image (no-op if already cached)
        try:
            await loop.run_in_executor(None, client.images.get, image)
        except Exception:
            logger.info("Pulling image %s on host %s", image, host_id)
            await loop.run_in_executor(None, lambda: client.images.pull(image))

        container = await loop.run_in_executor(
            None,
            lambda: client.containers.run(
                image=image,
                name=name,
                command="sleep infinity",
                detach=True,
                mem_limit=self._settings.container_mem_limit,
                nano_cpus=self._settings.container_cpu_limit,
                network_mode="bridge",
                extra_hosts={"host.docker.internal": "host-gateway"},
                remove=False,
            ),
        )
        # Pre-create /input
        await loop.run_in_executor(
            None,
            lambda: container.exec_run(["mkdir", "-p", "/input"]),
        )
        return container

    async def _refill_loop(self, host_url: str) -> None:
        host_id = _host_id(host_url)
        target = self._settings.WARM_POOL_SIZE if self._settings.WARM_POOL_SIZE > 0 else 2

        while self._running:
            try:
                available = await self._redis.pool_available_count(host_id)
                if available < target:
                    got_lock = await self._redis.pool_acquire_create_lock(host_id)
                    if got_lock:
                        try:
                            available = await self._redis.pool_available_count(host_id)
                            if available >= target:
                                continue
                            container = await self._create_container(host_url, host_id)
                            await self._redis.pool_push(host_id, container.id)
                            logger.debug("Pool refill host=%s container=%s available=%d", host_id, container.id[:12], available + 1)
                        finally:
                            await self._redis.pool_release_create_lock(host_id)
                await asyncio.sleep(2)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Pool refill error host=%s: %s", host_id, exc)
                await asyncio.sleep(5)


def _host_id(host_url: str) -> str:
    """Derive a short ID from the Docker host URL."""
    import hashlib
    return hashlib.md5(host_url.encode()).hexdigest()[:8]
