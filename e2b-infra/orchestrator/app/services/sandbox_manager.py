"""Sandbox manager — container lifecycle + exec + SSE streaming via docker-py SDK."""
from __future__ import annotations

import asyncio
import io
import json
import logging
import tarfile
import time
import uuid
from collections.abc import AsyncIterator

from docker.models.containers import Container

from app.config import Settings
from app.services.pool_manager import PoolManager
from app.services.redis_store import RedisStore

logger = logging.getLogger(__name__)


class SandboxManager:
    def __init__(self, settings: Settings, redis_store: RedisStore, pool_manager: PoolManager):
        self._settings = settings
        self._redis = redis_store
        self._pool = pool_manager
        self._instance_id = str(uuid.uuid4())
        self._active_streams: dict[str, Container] = {}
        self._heartbeat_task: asyncio.Task | None = None
        self._cleanup_task: asyncio.Task | None = None

    async def start(self) -> None:
        await self._pool.start()
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info("SandboxManager started instance=%s", self._instance_id)

    async def stop(self) -> None:
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        if self._cleanup_task:
            self._cleanup_task.cancel()

        # Terminate locally-owned runs
        for run_id in list(self._active_streams.keys()):
            await self.terminate_run(run_id)

        # Drain warm pool (destroys containers + clears pool Redis keys)
        await self._pool.stop()

        # Remove this instance's heartbeat + registration
        await self._redis.deregister()

        # Clear all remaining orchestrator-scoped Redis keys
        await self._redis.flush_orchestrator_keys()
        logger.info("SandboxManager stopped")

    async def get_stats(self) -> dict:
        hosts = self._pool.get_hosts()
        host_stats = []
        total_available = 0
        for host_url in hosts:
            from app.services.pool_manager import _host_id
            host_id = _host_id(host_url)
            available = await self._redis.pool_available_count(host_id)
            total = await self._redis.pool_total(host_id)
            total_available += available
            host_stats.append({"host_id": host_id, "available": available, "total": total})
        return {
            "active_runs": len(self._active_streams),
            "pool_available": total_available,
            "hosts": host_stats,
            "instance_id": self._instance_id,
        }

    # ── Sandbox lifecycle ──

    async def create_sandbox(self) -> dict:
        container, host_id, pool_hit = await self._pool.acquire(
            image=self._settings.TEMPLATE_ID,
        )
        sandbox_id = str(uuid.uuid4())
        sandbox_data = {
            "sandbox_id": sandbox_id,
            "container_id": container.id,
            "host_id": host_id,
            "instance_id": self._instance_id,
            "pool_hit": pool_hit,
            "status": "running",
            "created_at": time.time(),
        }
        await self._redis.save_sandbox(sandbox_id, sandbox_data)
        self._active_streams[sandbox_id] = container
        logger.info("Sandbox created sandbox_id=%s container=%s pool=%s", sandbox_id, container.id[:12], pool_hit)
        return sandbox_data

    async def destroy_sandbox(self, sandbox_id: str) -> bool:
        sandbox = await self._redis.get_sandbox(sandbox_id)
        if not sandbox:
            return False

        container_id = sandbox.get("container_id")
        host_id = sandbox.get("host_id", "")
        container = self._active_streams.pop(sandbox_id, None)

        if not container and container_id:
            try:
                client = self._pool.get_client(host_id)
                container = client.containers.get(container_id)
            except Exception:
                pass

        if container:
            await self._destroy_container(container)
        await self._pool.release(host_id)
        await self._redis.delete_sandbox(sandbox_id)
        return True

    # ── Exec (snapshot + runner + SSE streaming) ──

    async def exec_agent(
        self,
        sandbox_id: str,
        run_id: str,
        snapshot_json: str,
    ) -> AsyncIterator[dict]:
        sandbox = await self._redis.get_sandbox(sandbox_id)
        if not sandbox:
            yield {"event": "error", "data": {"error": "sandbox not found"}}
            return

        container = self._active_streams.get(sandbox_id)
        if not container:
            # Cross-instance lookup
            container_id = sandbox.get("container_id")
            host_id = sandbox.get("host_id", "")
            if container_id and host_id:
                client = self._pool.get_client(host_id)
                try:
                    container = client.containers.get(container_id)
                except Exception:
                    yield {"event": "error", "data": {"error": "container not found"}}
                    return
            else:
                yield {"event": "error", "data": {"error": "container not found"}}
                return

        run_data = {
            "run_id": run_id,
            "sandbox_id": sandbox_id,
            "container_id": container.id,
            "host_id": sandbox.get("host_id", ""),
            "instance_id": self._instance_id,
            "status": "running",
            "created_at": time.time(),
        }
        await self._redis.save_run(run_id, run_data)

        t0 = time.perf_counter()
        try:
            await self._write_snapshot(container, snapshot_json)
            async for event in self._exec_and_stream(container, run_id):
                yield event
        except Exception as exc:
            logger.error("Exec agent failed run_id=%s: %s", run_id, exc)
            yield {"event": "error", "data": {"error": str(exc), "error_type": type(exc).__name__}}
        finally:
            await self._redis.update_run_status(run_id, "done")
            logger.info("[TIMING] exec_agent run_id=%s elapsed_ms=%.2f", run_id, (time.perf_counter() - t0) * 1000)

    async def terminate_run(self, run_id: str) -> bool:
        """Terminate a running exec by destroying its container."""
        run_data = await self._redis.get_run(run_id)
        if not run_data:
            return False

        sandbox_id = run_data.get("sandbox_id", "")
        await self.destroy_sandbox(sandbox_id)
        await self._redis.delete_run(run_id)
        return True

    # ── Internal ──

    async def _write_snapshot(self, container: Container, snapshot_json: str) -> None:
        loop = asyncio.get_running_loop()
        data = snapshot_json.encode("utf-8")

        await loop.run_in_executor(None, lambda: container.exec_run(["mkdir", "-p", "/input"]))
        tar_stream = _build_tar("snapshot.json", data)
        await loop.run_in_executor(None, lambda: container.put_archive("/input", tar_stream))

    async def _exec_and_stream(self, container: Container, run_id: str) -> AsyncIterator[dict]:
        loop = asyncio.get_running_loop()

        exec_id = await loop.run_in_executor(
            None,
            lambda: container.client.api.exec_create(
                container.id,
                ["python", "-m", "runtime", "--config", "/input/snapshot.json", "--stream"],
                stdout=True,
                stderr=True,
            ),
        )

        # demux=True yields (stdout_bytes, stderr_bytes) per frame
        frame_gen = await loop.run_in_executor(
            None,
            lambda: container.client.api.exec_start(exec_id, detach=False, stream=True, socket=False, demux=True),
        )

        stderr_buf = ""
        done = False

        def _next_chunk():
            nonlocal stderr_buf, done
            try:
                stdout_bytes, stderr_bytes = next(frame_gen)
            except StopIteration:
                done = True
                return ""
            if stderr_bytes:
                stderr_text = stderr_bytes.decode("utf-8", errors="replace") if isinstance(stderr_bytes, bytes) else stderr_bytes
                stderr_buf += stderr_text
                while "\n" in stderr_buf:
                    line, stderr_buf = stderr_buf.split("\n", 1)
                    if line.strip():
                        logger.warning("[runner stderr] %s", line.strip())
            if stdout_bytes:
                return stdout_bytes.decode("utf-8", errors="replace") if isinstance(stdout_bytes, bytes) else stdout_bytes
            return ""

        buf = ""
        while not done:
            chunk = await loop.run_in_executor(None, _next_chunk)
            buf += chunk
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                # Agent-runtime outputs JSON Lines to stdout.
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    logger.debug("Non-JSON exec output: %s", line[:200])
                    continue
                yield {
                    "run_id": run_id,
                    "event": payload.get("event", "message"),
                    "data": payload.get("data", payload),
                }

    async def _destroy_container(self, container: Container) -> None:
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, lambda: container.remove(force=True))
        except Exception as exc:
            logger.warning("Failed to remove container %s: %s", container.id[:12], exc)

    async def _heartbeat_loop(self) -> None:
        while True:
            try:
                await self._redis.heartbeat()
                await asyncio.sleep(15)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Heartbeat error: %s", exc)
                await asyncio.sleep(5)

    async def _cleanup_loop(self) -> None:
        """Scan for runs from dead orchestrator instances and clean up their containers."""
        while True:
            try:
                await asyncio.sleep(30)
                active_instances = await self._redis.get_active_instances()
                for run_id in list(await self._redis.get_active_runs()):
                    run_data = await self._redis.get_run(run_id)
                    if not run_data:
                        continue
                    owner = run_data.get("instance_id", "")
                    if owner and owner != self._instance_id and owner not in active_instances:
                        logger.warning("Orphan run detected: %s from dead instance %s", run_id, owner)
                        await self.terminate_run(run_id)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Cleanup loop error: %s", exc)


def _build_tar(filename: str, data: bytes) -> io.BytesIO:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(name=filename)
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    buf.seek(0)
    return buf
