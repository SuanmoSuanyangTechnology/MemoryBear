"""The bridge works with any transport, not just Celery."""

from __future__ import annotations

import asyncio

import pytest

from task_bridge import (
    TaskBridge,
    TaskStatus,
    TaskTransport,
    TaskWatch,
)


class InMemoryTransport(TaskTransport):
    """A sync-only transport that proves the abstraction boundary."""

    def __init__(self):
        self.submissions = []
        self.statuses: dict[str, TaskStatus] = {}
        self.revoked = []
        self.status_reads: list[str] = []
        self.closed = False

    def submit(self, submission):
        self.submissions.append(submission)
        task_id = submission.task_id or f"task-{len(self.submissions)}"
        self.statuses[task_id] = TaskStatus.from_meta(
            task_id, {"status": "PENDING", "result": None}
        )
        return task_id

    def get_status(self, task_id):
        self.status_reads.append(task_id)
        return self.statuses[task_id]

    def revoke(self, task_id, *, terminate=False, signal=None):
        self.revoked.append((task_id, terminate, signal))

    def close(self):
        self.closed = True

    def complete(self, task_id, value):
        self.statuses[task_id] = TaskStatus.from_meta(
            task_id, {"status": "SUCCESS", "result": value}
        )


class EventWatch(TaskWatch):
    def __init__(self):
        self.event = asyncio.Event()
        self.closed = False

    def clear(self):
        self.event.clear()

    async def wait(self, timeout):
        try:
            await asyncio.wait_for(self.event.wait(), timeout=timeout)
        except TimeoutError:
            pass

    async def aclose(self):
        self.closed = True


class WatchingTransport(InMemoryTransport):
    def __init__(self):
        super().__init__()
        self.watch_handle = EventWatch()

    async def watch(self, task_id):
        return self.watch_handle


def test_bridge_builds_a_transport_neutral_submission():
    transport = InMemoryTransport()
    bridge = TaskBridge(transport)

    task_id = bridge.submit(
        "demo.task",
        args=[1],
        kwargs={"a": 2},
        queue="q",
        task_id="fixed",
        priority=3,
        shadow="renamed",
    )

    assert task_id == "fixed"
    submission = transport.submissions[0]
    assert submission.task_name == "demo.task"
    assert submission.args == (1,)
    assert submission.kwargs == {"a": 2}
    assert submission.queue == "q"
    assert submission.task_id == "fixed"
    assert submission.options == {"priority": 3, "shadow": "renamed"}


def test_bridge_delegates_status_revoke_and_lifecycle():
    transport = InMemoryTransport()
    bridge = TaskBridge(transport)
    task_id = bridge.submit("demo.task")

    assert bridge.get_status(task_id).pending
    bridge.revoke(task_id, terminate=True, signal="SIGKILL")
    bridge.warm_up(connections=2)
    bridge.close()

    assert transport.revoked == [(task_id, True, "SIGKILL")]
    assert transport.closed is True


async def test_async_api_offloads_a_sync_transport():
    transport = InMemoryTransport()
    bridge = TaskBridge(transport)

    task_id = await bridge.asubmit("demo.task", args=[1])
    transport.complete(task_id, 42)
    status = await bridge.await_result(task_id, timeout=1, interval=0.001)

    assert status.result == 42


def test_status_many_deduplicates_ids():
    transport = InMemoryTransport()
    bridge = TaskBridge(transport)
    task_id = bridge.submit("demo.task")

    statuses = bridge.get_status_many([task_id, task_id])

    assert list(statuses) == [task_id]
    assert transport.status_reads == [task_id]


def test_status_many_rejects_a_single_string_id():
    transport = InMemoryTransport()
    bridge = TaskBridge(transport)
    task_id = bridge.submit("demo.task")

    for bad in (task_id, task_id.encode()):
        with pytest.raises(TypeError, match="single string"):
            bridge.get_status_many(bad)

    assert transport.status_reads == []


async def test_async_status_many_rejects_a_single_string_id():
    transport = InMemoryTransport()
    bridge = TaskBridge(transport)
    task_id = bridge.submit("demo.task")

    with pytest.raises(TypeError, match="single string"):
        await bridge.aget_status_many(task_id)

    assert transport.status_reads == []


async def test_a_transport_can_push_a_wake_up():
    transport = WatchingTransport()
    bridge = TaskBridge(transport)
    task_id = await bridge.asubmit("demo.task")

    async def complete_later():
        await asyncio.sleep(0.02)
        transport.complete(task_id, 99)
        transport.watch_handle.event.set()

    task = asyncio.create_task(complete_later())
    started = asyncio.get_running_loop().time()
    status = await bridge.await_result(task_id, timeout=5, interval=10)
    elapsed = asyncio.get_running_loop().time() - started
    await task

    assert status.result == 99
    assert elapsed < 0.5  # polling alone would have slept ~10s
    assert transport.watch_handle.closed is True


def test_bridge_requires_a_transport():
    with pytest.raises(TypeError):
        TaskBridge(object())
