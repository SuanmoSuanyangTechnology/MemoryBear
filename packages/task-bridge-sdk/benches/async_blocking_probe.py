"""Measure where the async bridge blocks and where it only queues.

Scenarios:

1. baseline          -- event-loop heartbeat with no work (timer-resolution floor);
2. real status       -- N concurrent ``aget_status`` against the real Redis backend,
                        first burst (connection warm-up) and warm burst;
3. long waits        -- N concurrent ``await_result(timeout=T)``; if each wait held a
                        thread, wall time would be ``ceil(N/workers) * T``;
4. pool saturation   -- occupy every default-executor thread with slow backend reads,
                        then time an unrelated short ``to_thread`` call;
5. cancelled wait    -- cancel N in-flight reads; the threads keep running, so the
                        short call still queues (cancellation does not interrupt a thread);
6. sync control      -- call the synchronous ``get_status`` on the event loop;
7. real broker       -- N concurrent ``asubmit`` to RabbitMQ on a throwaway queue.

Run with the package's src on PYTHONPATH::

    PYTHONPATH=src python benches/async_blocking_probe.py
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from task_bridge import CeleryBridge, CeleryConfig, TaskTimeoutError

HEARTBEAT_INTERVAL = 0.002
REPO_ROOT = Path(__file__).resolve().parents[3]


class Heartbeat:
    """Records how long the event loop ever failed to run this coroutine."""

    def __init__(self, interval: float = HEARTBEAT_INTERVAL) -> None:
        self.interval = interval
        self.gaps: list[float] = []
        self._task: asyncio.Task | None = None

    async def _run(self) -> None:
        # perf_counter, not loop.time(): on Windows the loop clock is
        # quantised to 15.6ms and would hide sub-tick gaps.
        last = time.perf_counter()
        while True:
            await asyncio.sleep(self.interval)
            now = time.perf_counter()
            self.gaps.append(now - last)
            last = now

    async def __aenter__(self) -> Heartbeat:
        self._task = asyncio.create_task(self._run())
        # Wait for a couple of baseline samples before the workload starts, so
        # a blocking gap is never the only sample in the window.
        while len(self.gaps) < 2:
            await asyncio.sleep(self.interval)
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        assert self._task is not None
        # A heartbeat whose sleep expired while the loop was blocked is ready
        # to run; give it a full Windows timer tick to record the gap.
        await asyncio.sleep(self.interval * 8)
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass

    def summary(self) -> dict[str, float]:
        samples = sorted(self.gaps[1:]) or [0.0]
        p95 = samples[min(len(samples) - 1, int(round(0.95 * (len(samples) - 1))))]
        return {"hb_max_ms": max(samples) * 1000, "hb_p95_ms": p95 * 1000}


class SlowBackend:
    """A result backend whose read takes ``seconds`` and cannot be interrupted."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds

    def get_task_meta(self, task_id: str) -> dict:
        time.sleep(self.seconds)
        return {"status": "PENDING", "result": None}


class FakeApp:
    def __init__(self, backend: SlowBackend) -> None:
        self.backend = backend

    def close(self) -> None:
        pass


def pctl(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(p * (len(ordered) - 1))))]


def load_api_env() -> dict[str, str]:
    path = REPO_ROOT / "api" / ".env"
    env: dict[str, str] = {}
    if not path.is_file():
        return env
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip()
    return env


def redis_url(env: dict[str, str], db: int) -> str:
    host = env.get("REDIS_HOST", "127.0.0.1")
    port = env.get("REDIS_PORT", "6379")
    password = env.get("REDIS_PASSWORD", "")
    auth = f":{quote(password, safe='')}@" if password else ""
    return f"redis://{auth}{host}:{port}/{db}"


# ── scenarios ────────────────────────────────────────────────────────────


async def baseline() -> dict:
    async with Heartbeat() as hb:
        await asyncio.sleep(1.0)
    return hb.summary()


async def real_status_probe(bridge: CeleryBridge, n: int) -> dict:
    async def burst() -> dict:
        ids = [f"task-bridge-probe:{uuid4()}" for _ in range(n)]

        async def one(task_id: str) -> float:
            started = time.perf_counter()
            await bridge.aget_status(task_id)
            return time.perf_counter() - started

        async with Heartbeat() as hb:
            started = time.perf_counter()
            latencies = await asyncio.gather(*(one(task_id) for task_id in ids))
            wall = time.perf_counter() - started

        return {
            "wall_ms": wall * 1000,
            "p50_ms": pctl(latencies, 0.50) * 1000,
            "p95_ms": pctl(latencies, 0.95) * 1000,
            **hb.summary(),
        }

    first = await burst()
    warm = await burst()
    return {
        "n": n,
        "first_wall_ms": first["wall_ms"],
        "first_p95_ms": first["p95_ms"],
        "first_hb_max_ms": first["hb_max_ms"],
        "warm_wall_ms": warm["wall_ms"],
        "warm_p95_ms": warm["p95_ms"],
        "warm_hb_max_ms": warm["hb_max_ms"],
    }


async def batch_status_probe(bridge: CeleryBridge, n: int) -> dict:
    ids = [f"task-bridge-probe:{uuid4()}" for _ in range(n)]

    async with Heartbeat() as hb:
        started = time.perf_counter()
        statuses = await bridge.aget_status_many(ids)
        wall = time.perf_counter() - started

    return {"n": n, "returned": len(statuses), "wall_ms": wall * 1000, **hb.summary()}


async def long_waits_probe(
    bridge: CeleryBridge, n: int, timeout: float, interval: float, workers: int
) -> dict:
    ids = [f"task-bridge-probe:{uuid4()}" for _ in range(n)]

    async def one(task_id: str) -> float:
        started = time.perf_counter()
        try:
            await bridge.await_result(task_id, timeout=timeout, interval=interval)
        except TaskTimeoutError:
            pass
        return time.perf_counter() - started

    async with Heartbeat() as hb:
        started = time.perf_counter()
        durations = await asyncio.gather(*(one(task_id) for task_id in ids))
        wall = time.perf_counter() - started

    return {
        "n": n,
        "timeout_s": timeout,
        "workers": workers,
        "wall_ms": wall * 1000,
        "would_be_ms_if_thread_held": (n / workers) * timeout * 1000,
        "wait_min_ms": min(durations) * 1000,
        "wait_max_ms": max(durations) * 1000,
        **hb.summary(),
    }


async def saturation_probe(workers: int, slow_seconds: float) -> dict:
    bridge = CeleryBridge(app=FakeApp(SlowBackend(slow_seconds)))

    started = time.perf_counter()
    await asyncio.to_thread(time.sleep, 0.05)
    idle_short_call = time.perf_counter() - started

    async with Heartbeat() as hb:
        tasks = [
            asyncio.create_task(bridge.aget_status(f"t{i}")) for i in range(workers)
        ]
        await asyncio.sleep(0.05)  # let every worker thread pick up a slow read
        started = time.perf_counter()
        await asyncio.to_thread(time.sleep, 0.05)
        queued_short_call = time.perf_counter() - started
        await asyncio.gather(*tasks)

    return {
        "workers": workers,
        "slow_read_s": slow_seconds,
        "idle_short_call_ms": idle_short_call * 1000,
        "queued_short_call_ms": queued_short_call * 1000,
        **hb.summary(),
    }


async def cancelled_reads_probe(workers: int, slow_seconds: float) -> dict:
    bridge = CeleryBridge(app=FakeApp(SlowBackend(slow_seconds)))
    tasks = [asyncio.create_task(bridge.aget_status(f"t{i}")) for i in range(workers)]
    await asyncio.sleep(0.05)

    started = time.perf_counter()
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    cancel_returned = time.perf_counter() - started

    started = time.perf_counter()
    await asyncio.to_thread(time.sleep, 0.05)
    short_call = time.perf_counter() - started

    return {
        "workers": workers,
        "slow_read_s": slow_seconds,
        "cancel_await_ms": cancel_returned * 1000,
        "short_call_after_cancel_ms": short_call * 1000,
    }


async def sync_control_probe(slow_seconds: float) -> dict:
    bridge = CeleryBridge(app=FakeApp(SlowBackend(slow_seconds)))
    async with Heartbeat() as hb:
        started = time.perf_counter()
        bridge.get_status("t")
        blocked = time.perf_counter() - started
    return {"blocked_ms": blocked * 1000, **hb.summary()}


async def broker_probe(bridge: CeleryBridge, n: int, queue: str) -> dict:
    async def one(index: int) -> float:
        started = time.perf_counter()
        await bridge.asubmit("task_bridge.probe", args=[index], queue=queue)
        return time.perf_counter() - started

    async def burst(offset: int) -> dict:
        async with Heartbeat() as hb:
            started = time.perf_counter()
            latencies = await asyncio.gather(*(one(offset + i) for i in range(n)))
            wall = time.perf_counter() - started
        return {
            "wall_ms": wall * 1000,
            "p50_ms": pctl(latencies, 0.50) * 1000,
            "p95_ms": pctl(latencies, 0.95) * 1000,
            **hb.summary(),
        }

    await bridge.asubmit("task_bridge.probe", args=[-1], queue=queue)  # warm-up
    first = await burst(0)
    warm = await burst(n)
    return {
        "n": n,
        "first_wall_ms": first["wall_ms"],
        "first_p95_ms": first["p95_ms"],
        "first_hb_max_ms": first["hb_max_ms"],
        "warm_wall_ms": warm["wall_ms"],
        "warm_p95_ms": warm["p95_ms"],
        "warm_hb_max_ms": warm["hb_max_ms"],
    }


# ── main ─────────────────────────────────────────────────────────────────


def show(title: str, data: dict) -> None:
    print(f"\n{title}")
    for key, value in data.items():
        if isinstance(value, float):
            print(f"  {key:36s} {value:10.1f}")
        else:
            print(f"  {key:36s} {value}")


async def main() -> None:
    loop = asyncio.get_running_loop()
    await asyncio.to_thread(lambda: None)
    workers = loop._default_executor._max_workers  # noqa: SLF001 - probe only
    print(f"default executor max_workers = {workers}")
    print(
        "note: heartbeat samples are event-loop scheduling gaps; "
        "on Windows the idle floor is already ~16ms"
    )

    env = load_api_env()
    backend = redis_url(env, int(env.get("REDIS_DB_CELERY_BACKEND", "2")))
    broker = env.get("CELERY_BROKER_URL") or redis_url(
        env, int(env.get("REDIS_DB_CELERY_BROKER", "3"))
    )
    print(f"result backend = {backend.split('@')[-1]}")
    print(f"broker         = {broker.split('@')[-1]}")

    show("1. baseline (idle event loop, 1s)", await baseline())

    real_queue = f"task_bridge_probe_{os.getpid()}"
    real_bridge = CeleryBridge(
        CeleryConfig(broker_url=broker, result_backend=backend, default_queue=real_queue)
    )
    try:
        await real_bridge.aget_status("task-bridge-probe:warmup")
        real_ok = True
    except Exception as exc:  # noqa: BLE001 - probe reports, does not fail
        real_ok = False
        print(f"\nreal backend unavailable ({type(exc).__name__}: {exc}); skipping Redis/RabbitMQ")
        await real_bridge.aclose()

    if real_ok:
        show(
            "2. real Redis: 200 concurrent aget_status (first vs warm burst)",
            await real_status_probe(real_bridge, 200),
        )
        show(
            "2b. real Redis: 200 statuses in one aget_status_many (MGET)",
            await batch_status_probe(real_bridge, 200),
        )
        show(
            "3. 100 concurrent await_result (timeout 1.5s, interval 0.1s)",
            await long_waits_probe(real_bridge, 100, 1.5, 0.1, workers),
        )
        try:
            show(
                "7. real RabbitMQ: 100 concurrent asubmit (throwaway queue)",
                await broker_probe(real_bridge, 100, real_queue),
            )
        except Exception as exc:  # noqa: BLE001 - probe reports, does not fail
            print(f"\nbroker probe failed: {type(exc).__name__}: {exc}")
        finally:
            try:
                with real_bridge.app.connection_for_write() as connection:
                    connection.default_channel.queue_delete(real_queue)
                print(f"  deleted queue {real_queue}")
            except Exception as exc:  # noqa: BLE001
                print(f"  queue cleanup failed: {type(exc).__name__}: {exc}")
        await real_bridge.aclose()

    show(
        "4. default pool saturated by slow backend reads",
        await saturation_probe(workers, 0.5),
    )
    show(
        "5. cancel 24 in-flight reads, then a 50ms to_thread call",
        await cancelled_reads_probe(workers, 1.0),
    )
    show(
        "6. control: sync get_status called on the event loop",
        await sync_control_probe(0.3),
    )


if __name__ == "__main__":
    asyncio.run(main())
