"""In-memory backend with fault injection, used by the scheduler tests.

Every real backend must behave the same way on the points the scheduler relies
on: missing instance -> ``InstanceGone``, missing context -> ``ContextGone``,
exec on a paused instance -> ``BackendError``.
"""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field

from ..clock import Clock
from .base import (
    KIND_DIR,
    KIND_FILE,
    STATE_PAUSED,
    STATE_RUNNING,
    BackendError,
    BackendUnavailable,
    ContextGone,
    FileEntry,
    FileNotFound,
    InstanceGone,
    InstanceHandle,
    InstanceInfo,
    InstanceSpec,
    IsADirectory,
    RunResult,
)

READ_CHUNK = 64 * 1024
DIR_SIZE = 4096

RunHook = Callable[[str, str, str], Awaitable[RunResult | None]]


@dataclass
class _Instance:
    instance_id: str
    metadata: dict[str, str]
    created_at_ms: int
    allow_internet_access: bool | None = None
    network: dict[str, object] | None = None
    state: str = STATE_RUNNING
    contexts: dict[str, str] = field(default_factory=dict)  # context_id -> language
    files: dict[str, tuple[bytes, int]] = field(default_factory=dict)  # path -> (data, mtime)
    dirs: dict[str, int] = field(default_factory=lambda: {"/workspace": 0})  # path -> mtime


def _ancestors(path: str) -> list[str]:
    parts = [p for p in path.split("/") if p]
    return ["/" + "/".join(parts[:i]) for i in range(1, len(parts))]


class FakeBackend:
    name = "fake"

    def __init__(self, clock: Clock | None = None) -> None:
        self._clock = clock or Clock()
        self._instances: dict[str, _Instance] = {}
        self._seq = itertools.count(1)
        # Fault injection: operation name -> number of upcoming calls that fail.
        self.fail: dict[str, int] = {}
        # When set, ``create`` blocks until the event is set (concurrency tests).
        self.create_gate: asyncio.Event | None = None
        # Optional hook run inside ``run_code``; may return a custom result.
        self.run_hook: RunHook | None = None
        self.created_total = 0
        self.paused_total = 0
        self.resumed_total = 0
        self.destroyed_total = 0
        self.contexts_created_total = 0

    # -- helpers for tests ----------------------------------------------------

    def _maybe_fail(self, op: str) -> None:
        remaining = self.fail.get(op, 0)
        if remaining > 0:
            self.fail[op] = remaining - 1
            raise BackendUnavailable(f"injected {op} failure")

    def _get(self, instance_id: str) -> _Instance:
        inst = self._instances.get(instance_id)
        if inst is None:
            raise InstanceGone(instance_id)
        return inst

    def instance_ids(self) -> list[str]:
        return sorted(self._instances)

    def state_of(self, instance_id: str) -> str | None:
        inst = self._instances.get(instance_id)
        return inst.state if inst else None

    def network_of(self, instance_id: str) -> tuple[bool | None, dict[str, object] | None]:
        inst = self._get(instance_id)
        return inst.allow_internet_access, inst.network

    def lose_instance(self, instance_id: str) -> None:
        self._instances.pop(instance_id, None)

    def lose_context(self, instance_id: str, context_id: str) -> None:
        self._get(instance_id).contexts.pop(context_id, None)

    def inject_orphan(self, metadata: dict[str, str], created_at_ms: int) -> str:
        instance_id = f"fake-{next(self._seq)}"
        self._instances[instance_id] = _Instance(instance_id, dict(metadata), created_at_ms)
        return instance_id

    # -- SandboxBackend -------------------------------------------------------

    async def create(self, spec: InstanceSpec) -> InstanceHandle:
        self._maybe_fail("create")
        if self.create_gate is not None:
            await self.create_gate.wait()
        else:
            await asyncio.sleep(0)
        instance_id = f"fake-{next(self._seq)}"
        now = self._clock.now_ms()
        self._instances[instance_id] = _Instance(
            instance_id,
            dict(spec.metadata),
            now,
            spec.allow_internet_access,
            dict(spec.network) if spec.network is not None else None,
        )
        self.created_total += 1
        return InstanceHandle(instance_id, now)

    async def pause(self, instance_id: str) -> None:
        self._maybe_fail("pause")
        inst = self._get(instance_id)
        inst.state = STATE_PAUSED
        self.paused_total += 1

    async def resume(self, instance_id: str) -> None:
        self._maybe_fail("resume")
        inst = self._get(instance_id)
        inst.state = STATE_RUNNING
        self.resumed_total += 1

    async def destroy(self, instance_id: str) -> None:
        self._maybe_fail("destroy")
        self._get(instance_id)
        del self._instances[instance_id]
        self.destroyed_total += 1

    async def list_owned(self) -> list[InstanceInfo]:
        self._maybe_fail("list")
        return [
            InstanceInfo(i.instance_id, dict(i.metadata), i.state, i.created_at_ms)
            for i in self._instances.values()
        ]

    async def create_context(self, instance_id: str, language: str) -> str:
        self._maybe_fail("create_context")
        inst = self._get(instance_id)
        if inst.state != STATE_RUNNING:
            raise BackendError("instance is paused")
        context_id = f"ctx-{next(self._seq)}"
        inst.contexts[context_id] = language
        self.contexts_created_total += 1
        return context_id

    async def run_code(
        self, instance_id: str, context_id: str, code: str, timeout_s: float
    ) -> RunResult:
        inst = self._get(instance_id)
        if inst.state != STATE_RUNNING:
            raise BackendError("instance is paused")
        if context_id not in inst.contexts:
            raise ContextGone(context_id)
        if self.run_hook is not None:
            custom = await self.run_hook(instance_id, context_id, code)
            if custom is not None:
                return custom
        return RunResult(stdout=code)

    async def delete_context(self, instance_id: str, context_id: str) -> None:
        inst = self._get(instance_id)
        if inst.state != STATE_RUNNING:
            raise BackendError("instance is paused")
        if inst.contexts.pop(context_id, None) is None:
            raise ContextGone(context_id)

    async def restart_context(self, instance_id: str, context_id: str) -> None:
        inst = self._get(instance_id)
        if context_id not in inst.contexts:
            raise ContextGone(context_id)

    # -- files ----------------------------------------------------------------

    def _fs(self, instance_id: str) -> _Instance:
        self._maybe_fail("files")
        inst = self._get(instance_id)
        if inst.state != STATE_RUNNING:
            raise BackendError("instance is paused")
        return inst

    @staticmethod
    def _entry(path: str, kind: str, size: int, mtime: int) -> FileEntry:
        return FileEntry(path.rsplit("/", 1)[-1], path, kind, size, mtime)

    def _stat(self, inst: _Instance, path: str) -> FileEntry:
        if path in inst.files:
            data, mtime = inst.files[path]
            return self._entry(path, KIND_FILE, len(data), mtime)
        if path in inst.dirs:
            return self._entry(path, KIND_DIR, DIR_SIZE, inst.dirs[path])
        raise FileNotFound(path)

    async def stat(self, instance_id: str, path: str) -> FileEntry:
        return self._stat(self._fs(instance_id), path)

    async def list_dir(self, instance_id: str, path: str, depth: int = 1) -> list[FileEntry]:
        inst = self._fs(instance_id)
        if path in inst.files:
            raise IsADirectory(path)
        if path not in inst.dirs:
            raise FileNotFound(path)
        prefix = path.rstrip("/") + "/"
        found = []
        for candidate in sorted(set(inst.files) | set(inst.dirs)):
            if candidate.startswith(prefix) and candidate[len(prefix):].count("/") < depth:
                found.append(self._stat(inst, candidate))
        return found

    async def read_stream(self, instance_id: str, path: str) -> AsyncIterator[bytes]:
        inst = self._fs(instance_id)
        if path in inst.dirs:
            raise IsADirectory(path)
        if path not in inst.files:
            raise FileNotFound(path)
        data = inst.files[path][0]
        for start in range(0, len(data), READ_CHUNK):
            await asyncio.sleep(0)
            yield data[start : start + READ_CHUNK]

    async def write_stream(
        self, instance_id: str, path: str, chunks: AsyncIterator[bytes]
    ) -> int:
        inst = self._fs(instance_id)
        if path in inst.dirs:
            raise IsADirectory(path)
        parts = []
        async for chunk in chunks:
            parts.append(chunk)
        data = b"".join(parts)
        now = self._clock.now_ms()
        for parent in _ancestors(path):
            inst.dirs.setdefault(parent, now)
        inst.files[path] = (data, now)
        return len(data)

    async def make_dir(self, instance_id: str, path: str) -> None:
        inst = self._fs(instance_id)
        if path in inst.files:
            raise BackendError("a file is in the way")
        now = self._clock.now_ms()
        for parent in [*_ancestors(path), path]:
            inst.dirs.setdefault(parent, now)

    async def remove(self, instance_id: str, path: str) -> None:
        inst = self._fs(instance_id)
        prefix = path.rstrip("/") + "/"
        for table in (inst.files, inst.dirs):
            for candidate in [c for c in table if c == path or c.startswith(prefix)]:
                del table[candidate]

    # -- test helper ------------------------------------------------------------

    def put_file(
        self,
        instance_id: str,
        path: str,
        data: bytes,
        mtime_ms: int | None = None,
    ) -> None:
        inst = self._get(instance_id)
        now = self._clock.now_ms() if mtime_ms is None else mtime_ms
        for parent in _ancestors(path):
            inst.dirs.setdefault(parent, now)
        inst.files[path] = (data, now)

    async def health(self) -> bool:
        return True
