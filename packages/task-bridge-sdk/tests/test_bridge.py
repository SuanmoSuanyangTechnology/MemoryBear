"""Bridge behaviour against a fake Celery app (no broker, no Redis)."""

from __future__ import annotations

import asyncio
import threading
import time
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from task_bridge import (
    BridgeConfig,
    BridgeConfigurationError,
    CeleryBridge,
    InvalidTaskPayloadError,
    TaskFailedError,
    TaskPublishError,
    TaskResultUnavailableError,
    TaskTimeoutError,
)
from task_bridge.transports.celery.redis_results import RedisResultNotifier, RedisResultReader


class FakeBackend:
    """Returns queued metas in order, repeating the last one."""

    def __init__(self, metas=None, *, sequence=None, error=None):
        self.metas = dict(metas or {})
        self.sequence = list(sequence or [])
        self.error = error
        self.reads: list[str] = []

    def get_task_meta(self, task_id, cache=True):
        self.reads.append(task_id)
        if self.error is not None:
            raise self.error
        if self.sequence:
            meta = self.sequence.pop(0) if len(self.sequence) > 1 else self.sequence[0]
            return meta
        return self.metas.get(task_id, {"status": "PENDING", "result": None})


class FakeReader:
    """Stands in for :class:`RedisResultReader` in async-path tests."""

    def __init__(self, metas=None):
        self.metas = dict(metas or {})
        self.reads: list[str] = []
        self.pings = 0
        self.warmed = 0
        self.closed = False

    async def get_meta(self, task_id):
        self.reads.append(task_id)
        return self.metas.get(task_id, {"status": "PENDING", "result": None})

    async def get_many_meta(self, task_ids):
        self.reads.extend(task_ids)
        return {
            task_id: self.metas.get(task_id, {"status": "PENDING", "result": None})
            for task_id in task_ids
        }

    async def ping(self):
        self.pings += 1

    async def warm_connections(self, connections):
        self.warmed += connections

    async def aclose(self):
        self.closed = True


class FakeAsyncClient:
    """Minimal async Redis surface used by :class:`RedisResultReader`."""

    def __init__(self, data):
        self.data = data
        self.closed = False

    async def get(self, key):
        return self.data.get(key)

    async def mget(self, keys):
        return [self.data.get(key) for key in keys]

    async def ping(self):
        return True

    async def aclose(self):
        self.closed = True


class FakeAsyncPool:
    max_connections = None

    def __init__(self):
        self.acquired: list[object] = []
        self.released: list[object] = []

    async def get_connection(self, command_name=None):
        connection = object()
        self.acquired.append(connection)
        return connection

    async def release(self, connection):
        self.released.append(connection)


class PooledAsyncClient:
    def __init__(self):
        self.connection_pool = FakeAsyncPool()
        self.closed = False

    async def aclose(self):
        self.closed = True


class DummyBackend:
    """The reader only needs Celery's key layout and decoder."""

    def get_key_for_task(self, task_id):
        return f"key-{task_id}"

    def decode_result(self, raw):
        return raw


class BatchBackend(FakeBackend):
    """Backend exposing the batch read surface Celery's Redis backend has."""

    def __init__(self, metas):
        super().__init__()
        self.raw = metas
        self.mget_calls: list[list[str]] = []

    def get_key_for_task(self, task_id):
        return f"celery-task-meta-{task_id}"

    def mget(self, keys):
        self.mget_calls.append(list(keys))
        return [self.raw.get(key) for key in keys]

    def decode_result(self, raw):
        return raw


class FakePubSub:
    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue()
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []
        self.closed = False
        self.fail_subscribe = False

    async def subscribe(self, *channels):
        if self.fail_subscribe:
            raise ConnectionError("subscribe failed")
        self.subscribed.extend(channels)

    async def unsubscribe(self, *channels):
        self.unsubscribed.extend(channels)

    async def get_message(self, timeout=None, ignore_subscribe_messages=False):
        try:
            return await asyncio.wait_for(self.queue.get(), timeout=timeout)
        except TimeoutError:
            return None

    async def aclose(self):
        self.closed = True


class FakePubSubClient:
    def __init__(self):
        self.pubsubs: list[FakePubSub] = []

    def pubsub(self, ignore_subscribe_messages=False):
        pubsub = FakePubSub()
        self.pubsubs.append(pubsub)
        return pubsub


class FlakyPubSubClient:
    """First SUBSCRIBE fails, later attempts succeed."""

    def __init__(self):
        self.pubsubs: list[FakePubSub] = []
        self.fail_next_subscribe = True

    def pubsub(self, ignore_subscribe_messages=False):
        pubsub = FakePubSub()
        if self.fail_next_subscribe:
            self.fail_next_subscribe = False
            pubsub.fail_subscribe = True
        self.pubsubs.append(pubsub)
        return pubsub


class BlockingPubSub(FakePubSub):
    def __init__(self):
        super().__init__()
        self.block_next = False
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def subscribe(self, *channels):
        if self.block_next:
            self.block_next = False
            self.started.set()
            await self.release.wait()
        await super().subscribe(*channels)


class BlockingPubSubClient:
    def __init__(self, block_first=False):
        self.pubsubs: list[BlockingPubSub] = []
        self._block_first = block_first

    def pubsub(self, ignore_subscribe_messages=False):
        pubsub = BlockingPubSub()
        if self._block_first:
            self._block_first = False
            pubsub.block_next = True
        self.pubsubs.append(pubsub)
        return pubsub


class FakeNotifier:
    def __init__(self):
        self.event = asyncio.Event()
        self.registered: list[str] = []
        self.unregistered: list[str] = []

    async def register(self, task_id):
        self.registered.append(task_id)
        return self.event

    async def unregister(self, task_id, event):
        self.unregistered.append(task_id)


class WakeReader(FakeReader):
    """First read PENDING, later reads SUCCESS; exposes a fake notifier."""

    def __init__(self, notifier):
        super().__init__()
        self._notifier = notifier
        self.calls = 0

    def notifier(self):
        return self._notifier

    async def get_meta(self, task_id):
        self.calls += 1
        if self.calls == 1:
            return {"status": "PENDING", "result": None}
        return {"status": "SUCCESS", "result": 42}


class ConcurrencyBackend(FakeBackend):
    """Records peak concurrent backend reads."""

    def __init__(self, delay=0.05):
        super().__init__()
        self.delay = delay
        self.active = 0
        self.peak = 0
        self.lock = threading.Lock()

    def get_task_meta(self, task_id, cache=True):
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            time.sleep(self.delay)
            return {"status": "PENDING", "result": None}
        finally:
            with self.lock:
                self.active -= 1


class FakeConnectionPool:
    max_connections = None

    def __init__(self):
        self.acquired: list[object] = []
        self.released: list[object] = []

    def get_connection(self, command_name=None):
        connection = object()
        self.acquired.append(connection)
        return connection

    def release(self, connection):
        self.released.append(connection)


class PoolBackend:
    """Mimics Celery's Redis backend: a client owning a connection pool."""

    def __init__(self):
        self.pool = FakeConnectionPool()
        self.client = SimpleNamespace(connection_pool=self.pool)


class CountingApp:
    """Exposes ``backend`` as a property to count cold-start resolution."""

    def __init__(self, backend):
        self._backend = backend
        self.backend_calls = 0

    @property
    def backend(self):
        self.backend_calls += 1
        time.sleep(0.02)  # widen the race window
        return self._backend

    def close(self):
        pass


class FakeControl:
    def __init__(self):
        self.revoked: list[tuple[str, dict]] = []

    def revoke(self, task_id, **kwargs):
        self.revoked.append((task_id, kwargs))


class FakeApp:
    def __init__(self, *, backend=None, error=None, task_id=None):
        self.backend = backend or FakeBackend()
        self.control = FakeControl()
        self.error = error
        self.task_id = task_id
        self.calls: list[tuple[str, dict]] = []
        self.closed = False

    def send_task(self, name, **kwargs):
        if self.error is not None:
            raise self.error
        self.calls.append((name, kwargs))
        if self.task_id is not None:
            return SimpleNamespace(id=self.task_id)
        return SimpleNamespace(id=kwargs.get("task_id") or "task-1")

    def close(self):
        self.closed = True


def make_bridge(app=None) -> CeleryBridge:
    return CeleryBridge(app=app or FakeApp())


# ── submit ───────────────────────────────────────────────────────────────


def test_submit_forwards_payload_and_options():
    app = FakeApp()
    bridge = make_bridge(app)

    task_id = bridge.submit(
        "app.tasks.do_refresh_user_tags",
        args=(1, "two"),
        kwargs={"workspace_id": "w1"},
        queue="memory_heavy_tasks",
        task_id="fixed-id",
        countdown=5,
        priority=3,
        headers={"trace_id": "t1"},
    )

    assert task_id == "fixed-id"
    name, call = app.calls[0]
    assert name == "app.tasks.do_refresh_user_tags"
    assert call == {
        "args": [1, "two"],
        "kwargs": {"workspace_id": "w1"},
        "queue": "memory_heavy_tasks",
        "task_id": "fixed-id",
        "countdown": 5,
        "priority": 3,
        "headers": {"trace_id": "t1"},
    }


def test_submit_omits_unset_options():
    app = FakeApp()
    make_bridge(app).submit("app.tasks.x")

    _, call = app.calls[0]
    assert call == {"args": [], "kwargs": {}}


def test_submit_accepts_extra_send_task_options():
    app = FakeApp()
    make_bridge(app).submit("app.tasks.x", shadow="renamed")

    _, call = app.calls[0]
    assert call["shadow"] == "renamed"


def test_submit_rejects_non_json_payload_before_publishing():
    app = FakeApp()
    bridge = make_bridge(app)

    with pytest.raises(InvalidTaskPayloadError):
        bridge.submit("app.tasks.x", kwargs={"when": datetime.now(UTC)})
    assert app.calls == []


def test_submit_rejects_non_json_headers_before_publishing():
    app = FakeApp()
    with pytest.raises(InvalidTaskPayloadError):
        make_bridge(app).submit("app.tasks.x", headers={"bad": object()})
    assert app.calls == []


def test_submit_rejects_string_args():
    with pytest.raises(InvalidTaskPayloadError):
        make_bridge().submit("app.tasks.x", args="abc")


def test_submit_rejects_empty_task_name():
    with pytest.raises(InvalidTaskPayloadError):
        make_bridge().submit("")


def test_validation_can_be_disabled():
    app = FakeApp()
    bridge = CeleryBridge(app=app, validate_payload=False)

    bridge.submit("app.tasks.x", kwargs={"bad": object()})

    assert app.calls


def test_submit_wraps_publish_errors():
    app = FakeApp(error=ConnectionError("broker down"))
    with pytest.raises(TaskPublishError) as info:
        make_bridge(app).submit("app.tasks.x")
    assert isinstance(info.value.__cause__, ConnectionError)


def test_submit_requires_an_id():
    app = FakeApp(task_id="")
    with pytest.raises(TaskPublishError):
        make_bridge(app).submit("app.tasks.x")


# ── status ───────────────────────────────────────────────────────────────


def test_get_status_reads_the_result_backend():
    backend = FakeBackend({"t1": {"status": "SUCCESS", "result": {"n": 1}}})
    status = make_bridge(FakeApp(backend=backend)).get_status("t1")

    assert status.task_id == "t1"
    assert status.state == "SUCCESS"
    assert status.successful is True
    assert status.ready is True
    assert status.terminal is True
    assert status.result == {"n": 1}
    assert backend.reads == ["t1"]


def test_get_status_reports_failure_text_and_traceback():
    exc = ValueError("boom")
    backend = FakeBackend(
        {"t1": {"status": "FAILURE", "result": exc, "traceback": "Traceback ..."}}
    )
    status = make_bridge(FakeApp(backend=backend)).get_status("t1")

    assert status.failed is True
    assert status.exception is exc
    assert status.result is None
    assert status.error == "ValueError: boom"
    assert status.traceback == "Traceback ..."


def test_get_status_handles_string_failure_payload():
    backend = FakeBackend({"t1": {"status": "FAILURE", "result": "plain failure"}})
    status = make_bridge(FakeApp(backend=backend)).get_status("t1")

    assert status.exception is None
    assert status.error == "plain failure"


def test_get_status_passes_custom_states_through():
    backend = FakeBackend({"t1": {"status": "PROGRESS", "result": {"percent": 40}}})
    status = make_bridge(FakeApp(backend=backend)).get_status("t1")

    assert status.state == "PROGRESS"
    assert status.terminal is False
    assert status.result == {"percent": 40}


def test_get_status_without_result_backend_is_a_configuration_error():
    backend = FakeBackend(error=NotImplementedError("Must implement the get method."))
    with pytest.raises(TaskResultUnavailableError, match="result backend"):
        make_bridge(FakeApp(backend=backend)).get_status("t1")


def test_get_status_wraps_backend_read_errors():
    backend = FakeBackend(error=ConnectionError("redis down"))
    with pytest.raises(TaskResultUnavailableError) as info:
        make_bridge(FakeApp(backend=backend)).get_status("t1")
    assert isinstance(info.value.__cause__, ConnectionError)


# ── waiting ──────────────────────────────────────────────────────────────


def test_wait_for_result_polls_until_terminal():
    backend = FakeBackend(
        sequence=[
            {"status": "PENDING", "result": None},
            {"status": "STARTED", "result": None},
            {"status": "SUCCESS", "result": 7},
        ]
    )
    status = make_bridge(FakeApp(backend=backend)).wait_for_result(
        "t1", timeout=2, interval=0.001
    )

    assert status.result == 7
    assert backend.reads == ["t1", "t1", "t1"]


def test_wait_for_result_times_out_with_last_status():
    backend = FakeBackend(sequence=[{"status": "STARTED", "result": None}])
    with pytest.raises(TaskTimeoutError) as info:
        make_bridge(FakeApp(backend=backend)).wait_for_result("t1", timeout=0)

    assert info.value.status.state == "STARTED"
    assert info.value.task_id == "t1"


def test_wait_for_result_can_raise_task_failure():
    backend = FakeBackend({"t1": {"status": "FAILURE", "result": ValueError("nope")}})
    with pytest.raises(TaskFailedError) as info:
        make_bridge(FakeApp(backend=backend)).wait_for_result(
            "t1", timeout=1, raise_on_error=True
        )
    assert info.value.task_id == "t1"
    assert "nope" in str(info.value)


def test_wait_for_result_validates_interval_and_timeout():
    bridge = make_bridge()
    with pytest.raises(ValueError):
        bridge.wait_for_result("t1", interval=0)
    with pytest.raises(ValueError):
        bridge.wait_for_result("t1", timeout=-1)


# ── async ────────────────────────────────────────────────────────────────


async def test_async_submit_and_await_result():
    backend = FakeBackend({"t1": {"status": "SUCCESS", "result": 42}})
    bridge = make_bridge(FakeApp(backend=backend, task_id="t1"))

    task_id = await bridge.asubmit("app.tasks.x", args=[1], queue="q")
    status = await bridge.await_result(task_id, timeout=1, interval=0.001)

    assert task_id == "t1"
    assert status.result == 42


async def test_async_await_result_times_out():
    backend = FakeBackend(sequence=[{"status": "PENDING", "result": None}])
    bridge = make_bridge(FakeApp(backend=backend))

    with pytest.raises(TaskTimeoutError):
        await bridge.await_result("t1", timeout=0)


async def test_backend_is_resolved_once_during_a_cold_concurrent_burst():
    app = CountingApp(FakeBackend())
    bridge = CeleryBridge(app=app)

    await asyncio.gather(*(bridge.aget_status(f"t{i}") for i in range(24)))

    assert app.backend_calls == 1


async def test_async_status_uses_the_reader_without_a_thread():
    bridge = CeleryBridge(app=FakeApp())
    bridge.transport._reader = FakeReader({"t1": {"status": "SUCCESS", "result": 5}})
    bridge.transport._reader_attempted = True
    calls = []

    async def fake_run(func, /, *args, **kwargs):
        calls.append(func)
        return func(*args, **kwargs)

    bridge.transport._run_blocking = fake_run  # type: ignore[method-assign]

    status = await bridge.aget_status("t1")

    assert status.result == 5
    assert calls == []


async def test_async_status_falls_back_without_a_reader():
    backend = FakeBackend({"t1": {"status": "SUCCESS", "result": 9}})
    bridge = CeleryBridge(app=FakeApp(backend=backend))

    status = await bridge.aget_status("t1")

    assert status.result == 9
    assert backend.reads == ["t1"]


async def test_async_status_many_reads_once():
    reader = FakeReader({"a": {"status": "SUCCESS", "result": 1}})
    bridge = CeleryBridge(app=FakeApp())
    bridge.transport._reader = reader
    bridge.transport._reader_attempted = True

    statuses = await bridge.aget_status_many(["a", "b"])

    assert reader.reads == ["a", "b"]
    assert statuses["a"].result == 1
    assert statuses["b"].pending


def test_status_many_uses_one_mget():
    backend = BatchBackend({"celery-task-meta-a": {"status": "SUCCESS", "result": 1}})
    bridge = CeleryBridge(app=FakeApp(backend=backend))

    statuses = bridge.get_status_many(["a", "b"])

    assert backend.mget_calls == [["celery-task-meta-a", "celery-task-meta-b"]]
    assert statuses["a"].result == 1
    assert statuses["b"].pending


def test_redis_reader_reuses_celery_key_layout_and_decoding():
    app = BridgeConfig(
        broker_url="memory://", result_backend="redis://localhost:6379/4"
    ).build_app()
    try:
        backend = app.backend
        key = backend.get_key_for_task("t1")
        payload = backend.encode(
            {"status": "SUCCESS", "result": {"ok": True}, "traceback": None}
        )
        reader = RedisResultReader(backend, FakeAsyncClient({key: payload}))

        meta = asyncio.run(reader.get_meta("t1"))
        missing = asyncio.run(reader.get_meta("missing"))

        assert meta["status"] == "SUCCESS"
        assert meta["result"] == {"ok": True}
        assert missing["status"] == "PENDING"
    finally:
        app.close()


def test_pubsub_uses_a_dedicated_redis_client():
    bridge = CeleryBridge(
        BridgeConfig(
            broker_url="memory://",
            result_backend="redis://localhost:6379/4",
            async_redis_max_connections=1,
        )
    )
    reader = bridge.transport._get_result_reader()

    assert reader is not None
    assert reader._pubsub_client is not None
    assert reader._pubsub_client is not reader._client
    assert reader.notifier()._client is reader._pubsub_client

    asyncio.run(reader.aclose())
    bridge.close()


def test_reader_can_take_an_explicit_pubsub_client():
    shared = FakeAsyncClient({})
    dedicated = FakeAsyncClient({})
    reader = RedisResultReader(DummyBackend(), shared, pubsub_client=dedicated)

    assert reader.notifier()._client is dedicated
    asyncio.run(reader.aclose())
    assert shared.closed is True
    assert dedicated.closed is True


def test_reader_warm_connections_holds_the_requested_count():
    client = PooledAsyncClient()
    reader = RedisResultReader(DummyBackend(), client)

    asyncio.run(reader.warm_connections(4))

    assert len(client.connection_pool.acquired) == 4
    assert client.connection_pool.released == client.connection_pool.acquired


def test_reader_warm_connections_respects_the_pool_cap():
    client = PooledAsyncClient()
    client.connection_pool.max_connections = 2
    reader = RedisResultReader(DummyBackend(), client)

    with pytest.raises(ValueError, match="max_connections"):
        asyncio.run(reader.warm_connections(4))

    assert client.connection_pool.acquired == []


def test_async_reader_uses_celery_resolved_connection_params():
    from redis.asyncio import Connection as AsyncConnection

    bridge = CeleryBridge(
        BridgeConfig(broker_url="memory://", result_backend="redis://127.0.0.1:6379/4")
    )
    reader = bridge.transport._get_result_reader()

    assert reader is not None
    pool = reader._client.connection_pool
    assert pool.connection_class is AsyncConnection
    assert pool.connection_kwargs["host"] == "127.0.0.1"
    assert pool.connection_kwargs["port"] == 6379
    assert pool.connection_kwargs["db"] == 4

    asyncio.run(reader.aclose())
    bridge.close()


def test_async_reader_mirrors_celery_ssl_options():
    from ssl import CERT_REQUIRED

    from redis.asyncio import SSLConnection as AsyncSSLConnection

    bridge = CeleryBridge(
        BridgeConfig(
            broker_url="memory://",
            result_backend="rediss://127.0.0.1:6380/2",
            app_options={
                "redis_backend_use_ssl": {
                    "ssl_cert_reqs": "required",
                    "ssl_ca_certs": "/tmp/ca.pem",
                    "ssl_certfile": "/tmp/client.crt",
                    "ssl_keyfile": "/tmp/client.key",
                }
            },
        )
    )
    reader = bridge.transport._get_result_reader()

    assert reader is not None
    read_pool = reader._client.connection_pool
    assert read_pool.connection_class is AsyncSSLConnection
    assert read_pool.connection_kwargs["ssl_cert_reqs"] == CERT_REQUIRED
    assert read_pool.connection_kwargs["ssl_ca_certs"] == "/tmp/ca.pem"
    assert read_pool.connection_kwargs["ssl_certfile"] == "/tmp/client.crt"
    assert read_pool.connection_kwargs["ssl_keyfile"] == "/tmp/client.key"

    pubsub_pool = reader._pubsub_client.connection_pool
    assert pubsub_pool.connection_class is AsyncSSLConnection
    assert pubsub_pool.max_connections == 1
    assert pubsub_pool.connection_kwargs["ssl_ca_certs"] == "/tmp/ca.pem"

    asyncio.run(reader.aclose())
    bridge.close()


def test_redis_reader_is_created_for_a_redis_backend():
    bridge = CeleryBridge(
        BridgeConfig(broker_url="memory://", result_backend="redis://localhost:6379/4")
    )
    reader = bridge.transport._get_result_reader()

    assert reader is not None
    asyncio.run(reader.aclose())
    bridge.close()


def test_async_results_can_be_disabled():
    bridge = CeleryBridge(
        BridgeConfig(
            broker_url="memory://",
            result_backend="redis://localhost:6379/4",
            prefer_async_redis_results=False,
        )
    )

    assert bridge.transport._get_result_reader() is None
    bridge.close()


async def test_warm_up_opens_result_connections():
    bridge = CeleryBridge(app=FakeApp())
    reader = FakeReader()
    bridge.transport._reader = reader
    bridge.transport._reader_attempted = True

    await bridge.awarm_up(connections=3)

    assert reader.warmed == 3


async def test_warm_up_falls_back_to_status_reads():
    backend = FakeBackend()
    bridge = CeleryBridge(app=FakeApp(backend=backend))

    await bridge.awarm_up(connections=2)

    assert len(backend.reads) == 2


def test_sync_warm_up_opens_connections_in_parallel():
    """Sequential reads would reuse one pooled connection."""

    backend = ConcurrencyBackend()
    bridge = CeleryBridge(app=FakeApp(backend=backend))

    bridge.warm_up(connections=4)

    assert backend.peak == 4


def test_sync_warm_up_rejects_zero_connections():
    bridge = CeleryBridge(app=FakeApp())
    with pytest.raises(ValueError):
        bridge.warm_up(connections=0)


def test_warm_up_holds_pooled_connections_simultaneously():
    backend = PoolBackend()
    bridge = CeleryBridge(app=FakeApp(backend=backend))

    bridge.warm_up(connections=4)

    assert len(backend.pool.acquired) == 4
    assert backend.pool.released == backend.pool.acquired


def test_warm_up_rejects_more_connections_than_the_pool_allows():
    backend = PoolBackend()
    backend.pool.max_connections = 2
    bridge = CeleryBridge(app=FakeApp(backend=backend))

    with pytest.raises(ValueError, match="max_connections"):
        bridge.warm_up(connections=4)

    assert backend.pool.acquired == []


async def test_dedicated_executor_runs_publishes():
    app = FakeApp()
    threads = []
    original_send = app.send_task

    def recording_send(name, **kwargs):
        threads.append(threading.current_thread().name)
        return original_send(name, **kwargs)

    app.send_task = recording_send
    bridge = CeleryBridge(app=app, executor_workers=2)

    await asyncio.gather(*(bridge.asubmit("app.tasks.x") for _ in range(6)))

    assert len(threads) == 6
    assert all(name.startswith("task-bridge") for name in threads)
    bridge.close()


async def test_aclose_releases_the_async_reader():
    bridge = CeleryBridge(app=FakeApp())
    reader = FakeReader()
    bridge.transport._reader = reader
    bridge.transport._reader_attempted = True

    await bridge.aclose()

    assert reader.closed
    assert bridge.transport._reader is None


async def test_notifier_wakes_all_waiters_on_one_message():
    client = FakePubSubClient()
    notifier = RedisResultNotifier(client, lambda task_id: f"celery-task-meta-{task_id}")

    first = await notifier.register("t1")
    second = await notifier.register("t1")
    assert first is not None and second is not None

    pubsub = client.pubsubs[0]
    assert pubsub.subscribed == ["celery-task-meta-t1"]  # one SUBSCRIBE for both

    await pubsub.queue.put(
        {"type": "message", "channel": "celery-task-meta-t1", "data": b"x"}
    )
    await asyncio.wait_for(first.wait(), timeout=1)
    await asyncio.wait_for(second.wait(), timeout=1)

    await notifier.unregister("t1", first)
    assert pubsub.unsubscribed == []  # the second waiter keeps the channel
    await notifier.unregister("t1", second)
    assert pubsub.unsubscribed == ["celery-task-meta-t1"]

    await notifier.aclose()
    assert pubsub.closed


async def test_subscription_failure_does_not_poison_the_next_attempt():
    client = FlakyPubSubClient()
    notifier = RedisResultNotifier(client, lambda task_id: f"celery-task-meta-{task_id}")

    # First attempt fails: no waiter, no half-initialized PubSub is kept.
    assert await notifier.register("t1") is None
    assert client.pubsubs[0].closed is True
    assert notifier._pubsub is None
    assert notifier._events == {}
    assert notifier._channel_to_task == {}

    # The retry must establish a real connection AND start the listener.
    event = await notifier.register("t1")
    assert event is not None
    pubsub = client.pubsubs[1]
    assert pubsub.subscribed == ["celery-task-meta-t1"]
    assert notifier._listener is not None and not notifier._listener.done()

    await pubsub.queue.put({"channel": b"celery-task-meta-t1", "data": b"x"})
    await asyncio.wait_for(event.wait(), timeout=1)

    await notifier.unregister("t1", event)
    await notifier.aclose()


async def test_cancelled_initial_subscribe_leaves_no_state():
    client = BlockingPubSubClient(block_first=True)
    notifier = RedisResultNotifier(client, lambda task_id: f"celery-task-meta-{task_id}")

    task = asyncio.create_task(notifier.register("t1"))
    while not client.pubsubs:
        await asyncio.sleep(0)
    pubsub = client.pubsubs[0]
    await asyncio.wait_for(pubsub.started.wait(), timeout=1)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.01)  # the shielded close runs in the background

    assert notifier._events == {}
    assert notifier._channel_to_task == {}
    assert notifier._pubsub is None
    assert pubsub.closed is True

    await notifier.aclose()


async def test_cancelled_later_subscribe_keeps_existing_waiters():
    client = BlockingPubSubClient()
    notifier = RedisResultNotifier(client, lambda task_id: f"celery-task-meta-{task_id}")
    first = await notifier.register("t1")
    assert first is not None
    pubsub = client.pubsubs[0]

    pubsub.block_next = True
    task = asyncio.create_task(notifier.register("t2"))
    await asyncio.wait_for(pubsub.started.wait(), timeout=1)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert "t2" not in notifier._events
    assert "celery-task-meta-t2" not in notifier._channel_to_task
    assert first in notifier._events["t1"]

    await notifier.unregister("t1", first)
    await notifier.aclose()


async def test_register_restarts_a_listener_that_exited():
    client = FakePubSubClient()
    notifier = RedisResultNotifier(client, lambda task_id: f"celery-task-meta-{task_id}")
    first = await notifier.register("t1")
    assert first is not None
    listener = notifier._listener
    assert listener is not None

    listener.cancel()
    try:
        await listener
    except asyncio.CancelledError:
        pass

    # _pubsub is still set; registering a new task must restart the reader.
    event = await notifier.register("t2")
    assert event is not None
    assert notifier._listener is not None and not notifier._listener.done()

    pubsub = client.pubsubs[0]
    await pubsub.queue.put({"channel": b"celery-task-meta-t2", "data": b"x"})
    await asyncio.wait_for(event.wait(), timeout=1)

    await notifier.unregister("t1", first)
    await notifier.unregister("t2", event)
    await notifier.aclose()


async def test_notifier_normalizes_bytes_channels():
    """Celery's default key_t is bytes while PubSub reports bytes too."""

    client = FakePubSubClient()
    notifier = RedisResultNotifier(
        client, lambda task_id: f"celery-task-meta-{task_id}".encode()
    )

    event = await notifier.register("t1")
    pubsub = client.pubsubs[0]
    assert pubsub.subscribed == ["celery-task-meta-t1"]

    await pubsub.queue.put(
        {"type": "message", "channel": b"celery-task-meta-t1", "data": b"x"}
    )
    await asyncio.wait_for(event.wait(), timeout=1)

    await notifier.unregister("t1", event)
    await notifier.aclose()


async def test_notifier_falls_back_when_pubsub_is_unavailable():
    class BrokenClient:
        def pubsub(self, **kwargs):
            raise ConnectionError("pub/sub blocked")

    notifier = RedisResultNotifier(BrokenClient(), lambda task_id: f"k-{task_id}")

    assert await notifier.register("t1") is None
    await notifier.aclose()


async def test_await_result_wakes_on_pubsub_signal():
    notifier = FakeNotifier()
    reader = WakeReader(notifier)
    bridge = CeleryBridge(app=FakeApp())
    bridge.transport._reader = reader
    bridge.transport._reader_attempted = True

    async def signal_later():
        await asyncio.sleep(0.02)
        notifier.event.set()

    signal = asyncio.create_task(signal_later())
    started = asyncio.get_running_loop().time()
    status = await bridge.await_result("t1", timeout=5, interval=10)
    elapsed = asyncio.get_running_loop().time() - started
    await signal

    assert status.result == 42
    # Polling only would have slept ~10s; the signal wakes it in ~20ms.
    assert elapsed < 0.5
    assert notifier.registered == ["t1"]
    assert notifier.unregistered == ["t1"]


def test_pubsub_can_be_disabled():
    bridge = CeleryBridge(
        BridgeConfig(
            broker_url="memory://",
            result_backend="redis://localhost:6379/4",
            use_redis_pubsub=False,
        )
    )

    assert bridge.transport._get_result_notifier() is None
    bridge.close()


async def test_async_status_and_revoke():
    backend = FakeBackend({"t1": {"status": "SUCCESS", "result": 1}})
    app = FakeApp(backend=backend)
    bridge = make_bridge(app)

    assert (await bridge.aget_status("t1")).result == 1
    await bridge.arevoke("t1", terminate=True)
    assert app.control.revoked == [("t1", {"terminate": True, "signal": None})]


# ── control / lifecycle ──────────────────────────────────────────────────


def test_revoke_forwards_options():
    app = FakeApp()
    make_bridge(app).revoke("t1", terminate=True, signal="SIGKILL")

    assert app.control.revoked == [("t1", {"terminate": True, "signal": "SIGKILL"})]


def test_close_leaves_injected_apps_untouched():
    app = FakeApp()
    make_bridge(app).close()

    assert app.closed is False


def test_close_releases_owned_app_and_allows_rebuild():
    bridge = CeleryBridge(BridgeConfig(broker_url="memory://"))
    app = bridge.app
    closed: list[bool] = []
    app.close = lambda: closed.append(True)

    bridge.close()

    assert closed == [True]
    assert bridge.app is not app


def test_config_and_app_are_mutually_exclusive():
    with pytest.raises(BridgeConfigurationError):
        CeleryBridge(BridgeConfig(broker_url="memory://"), app=FakeApp())


def test_real_celery_app_publishes_and_polls_without_external_services():
    """Exercise the real Celery wiring on in-memory transports."""

    config = BridgeConfig(
        broker_url="memory://",
        result_backend="cache+memory://",
        default_queue="memory_tasks",
    )
    with CeleryBridge(config) as bridge:
        task_id = bridge.submit("app.tasks.demo", args=[1, "two"], queue="memory_tasks")
        status = bridge.get_status(task_id)

    assert task_id
    # Nothing consumes the message here, so the result backend has no meta.
    assert status.pending
