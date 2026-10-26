"""Backend contract: the behaviour every ``SandboxBackend`` must share.

The scheduler (``SandboxManager``) relies on exactly these points. A backend adds
its own thin ``test_contract_<name>.py`` that subclasses ``BackendContract`` and
provides a ``backend`` fixture; the whole suite then runs against it unchanged.

This module is not named ``test_*`` so pytest does not collect it directly.
Backends that need a live substrate should skip (not fail) when it is unreachable.
"""

from __future__ import annotations

import pytest

from src.backends import (
    KIND_DIR,
    KIND_FILE,
    META_KEY,
    META_TENANT,
    STATE_PAUSED,
    STATE_RUNNING,
    BackendError,
    ContextGone,
    FileNotFound,
    InstanceGone,
    InstanceSpec,
    IsADirectory,
    SandboxBackend,
)


async def chunked(data: bytes, size: int = 1000):
    for start in range(0, len(data), size):
        yield data[start : start + size]


async def read_all(backend: SandboxBackend, instance_id: str, path: str) -> bytes:
    return b"".join([c async for c in backend.read_stream(instance_id, path)])


def spec(digest: str = "d1", tenant: str = "t") -> InstanceSpec:
    return InstanceSpec(
        key_digest=digest,
        tenant_id=tenant,
        template="workspace",
        metadata={META_KEY: digest, META_TENANT: tenant},
    )


async def new_instance(backend: SandboxBackend, digest: str = "d1") -> str:
    return (await backend.create(spec(digest))).instance_id


async def info_of(backend: SandboxBackend, instance_id: str):
    return next((i for i in await backend.list_owned() if i.instance_id == instance_id), None)


class BackendContract:
    """Subclass and define a ``backend`` fixture returning a fresh backend."""

    def test_backend_satisfies_the_protocol(self, backend):
        assert isinstance(backend, SandboxBackend)
        assert backend.name

    async def test_health_is_true_for_a_working_backend(self, backend):
        assert await backend.health() is True

    # --- instance lifecycle -------------------------------------------------

    async def test_create_returns_distinct_handles_and_lists_them_running(self, backend):
        a = await backend.create(spec("a"))
        b = await backend.create(spec("b"))
        assert a.instance_id != b.instance_id
        info = await info_of(backend, a.instance_id)
        assert info is not None
        assert info.state == STATE_RUNNING
        assert info.created_at_ms == a.created_at_ms

    async def test_metadata_written_at_create_comes_back_from_list_owned(self, backend):
        instance_id = await new_instance(backend, "dx")
        info = await info_of(backend, instance_id)
        assert info is not None
        assert info.metadata[META_KEY] == "dx"
        assert info.metadata[META_TENANT] == "t"

    async def test_pause_and_resume_change_the_listed_state(self, backend):
        instance_id = await new_instance(backend)
        await backend.pause(instance_id)
        assert (await info_of(backend, instance_id)).state == STATE_PAUSED
        await backend.resume(instance_id)
        assert (await info_of(backend, instance_id)).state == STATE_RUNNING

    async def test_destroy_removes_the_instance_and_a_second_destroy_says_gone(self, backend):
        instance_id = await new_instance(backend)
        await backend.destroy(instance_id)
        assert await info_of(backend, instance_id) is None
        with pytest.raises(InstanceGone):
            await backend.destroy(instance_id)

    @pytest.mark.parametrize("op", ["pause", "resume", "destroy"])
    async def test_lifecycle_calls_on_unknown_instance_raise_instance_gone(self, backend, op):
        with pytest.raises(InstanceGone):
            await getattr(backend, op)("no-such-instance")

    # --- code contexts ------------------------------------------------------

    async def test_contexts_are_distinct_and_run_code_returns_a_result(self, backend):
        instance_id = await new_instance(backend)
        c1 = await backend.create_context(instance_id, "python3")
        c2 = await backend.create_context(instance_id, "python3")
        assert c1 != c2
        result = await backend.run_code(instance_id, c1, "print(1)", 5.0)
        assert result.error is None

    async def test_run_code_on_unknown_context_raises_context_gone(self, backend):
        instance_id = await new_instance(backend)
        with pytest.raises(ContextGone):
            await backend.run_code(instance_id, "no-such-context", "x", 5.0)

    async def test_delete_context_twice_raises_context_gone(self, backend):
        instance_id = await new_instance(backend)
        ctx = await backend.create_context(instance_id, "python3")
        await backend.delete_context(instance_id, ctx)
        with pytest.raises(ContextGone):
            await backend.delete_context(instance_id, ctx)
        with pytest.raises(ContextGone):
            await backend.run_code(instance_id, ctx, "x", 5.0)

    async def test_restart_context_keeps_it_usable_and_unknown_one_is_gone(self, backend):
        instance_id = await new_instance(backend)
        ctx = await backend.create_context(instance_id, "python3")
        await backend.restart_context(instance_id, ctx)
        assert (await backend.run_code(instance_id, ctx, "x", 5.0)).error is None
        with pytest.raises(ContextGone):
            await backend.restart_context(instance_id, "no-such-context")

    async def test_contexts_do_not_leak_between_instances(self, backend):
        a = await new_instance(backend, "a")
        b = await new_instance(backend, "b")
        ctx = await backend.create_context(a, "python3")
        with pytest.raises(ContextGone):
            await backend.run_code(b, ctx, "x", 5.0)

    @pytest.mark.parametrize(
        "op", ["create_context", "run_code", "delete_context", "restart_context"]
    )
    async def test_context_calls_on_unknown_instance_raise_instance_gone(self, backend, op):
        args = {
            "create_context": ("no-such-instance", "python3"),
            "run_code": ("no-such-instance", "ctx", "x", 5.0),
            "delete_context": ("no-such-instance", "ctx"),
            "restart_context": ("no-such-instance", "ctx"),
        }[op]
        with pytest.raises(InstanceGone):
            await getattr(backend, op)(*args)

    async def test_running_code_on_a_paused_instance_fails_without_claiming_it_is_gone(
        self, backend
    ):
        instance_id = await new_instance(backend)
        ctx = await backend.create_context(instance_id, "python3")
        await backend.pause(instance_id)
        with pytest.raises(BackendError) as excinfo:
            await backend.run_code(instance_id, ctx, "x", 5.0)
        # The manager treats Gone errors as "recreate"; a paused instance must not look gone.
        assert not isinstance(excinfo.value, (InstanceGone, ContextGone))
        await backend.resume(instance_id)
        assert (await backend.run_code(instance_id, ctx, "x", 5.0)).error is None

    # --- files --------------------------------------------------------------

    async def test_write_then_read_roundtrip_creates_parents_and_reports_size(self, backend):
        instance_id = await new_instance(backend)
        data = bytes(range(256)) * 20  # 5120 bytes, binary, spans several chunks
        path = "/workspace/sessions/c1/out/report.bin"
        assert await backend.write_stream(instance_id, path, chunked(data)) == len(data)
        assert await read_all(backend, instance_id, path) == data
        entry = await backend.stat(instance_id, path)
        assert (entry.kind, entry.size, entry.name, entry.path) == (
            KIND_FILE,
            len(data),
            "report.bin",
            path,
        )
        assert entry.mtime_ms > 0
        assert (await backend.stat(instance_id, "/workspace/sessions/c1/out")).kind == KIND_DIR

    async def test_write_overwrites_and_empty_file_is_allowed(self, backend):
        instance_id = await new_instance(backend)
        path = "/workspace/a.txt"
        await backend.write_stream(instance_id, path, chunked(b"first version"))
        await backend.write_stream(instance_id, path, chunked(b"v2"))
        assert await read_all(backend, instance_id, path) == b"v2"
        await backend.write_stream(instance_id, path, chunked(b""))
        assert await read_all(backend, instance_id, path) == b""
        assert (await backend.stat(instance_id, path)).size == 0

    async def test_missing_paths_raise_file_not_found(self, backend):
        instance_id = await new_instance(backend)
        with pytest.raises(FileNotFound):
            await backend.stat(instance_id, "/workspace/none")
        with pytest.raises(FileNotFound):
            await read_all(backend, instance_id, "/workspace/none")
        with pytest.raises(FileNotFound):
            await backend.list_dir(instance_id, "/workspace/none")

    async def test_reading_a_directory_raises_is_a_directory(self, backend):
        instance_id = await new_instance(backend)
        await backend.make_dir(instance_id, "/workspace/d")
        with pytest.raises(IsADirectory):
            await read_all(backend, instance_id, "/workspace/d")

    async def test_list_dir_depth_one_and_two_and_empty_dir(self, backend):
        instance_id = await new_instance(backend)
        await backend.write_stream(instance_id, "/workspace/d/a.txt", chunked(b"a"))
        await backend.write_stream(instance_id, "/workspace/d/sub/b.txt", chunked(b"bb"))
        await backend.make_dir(instance_id, "/workspace/empty")
        top = {e.name: e for e in await backend.list_dir(instance_id, "/workspace/d")}
        assert set(top) == {"a.txt", "sub"}
        assert (top["a.txt"].kind, top["a.txt"].size) == (KIND_FILE, 1)
        assert top["sub"].kind == KIND_DIR
        deep = {e.path for e in await backend.list_dir(instance_id, "/workspace/d", depth=2)}
        assert "/workspace/d/sub/b.txt" in deep
        assert await backend.list_dir(instance_id, "/workspace/empty") == []

    async def test_make_dir_is_idempotent_and_creates_parents(self, backend):
        instance_id = await new_instance(backend)
        await backend.make_dir(instance_id, "/workspace/x/y")
        await backend.make_dir(instance_id, "/workspace/x/y")
        assert (await backend.stat(instance_id, "/workspace/x/y")).kind == KIND_DIR
        assert (await backend.stat(instance_id, "/workspace/x")).kind == KIND_DIR

    async def test_remove_deletes_files_and_trees_and_ignores_missing(self, backend):
        instance_id = await new_instance(backend)
        await backend.write_stream(instance_id, "/workspace/t/a.txt", chunked(b"a"))
        await backend.write_stream(instance_id, "/workspace/t/s/b.txt", chunked(b"b"))
        await backend.remove(instance_id, "/workspace/t/a.txt")
        with pytest.raises(FileNotFound):
            await backend.stat(instance_id, "/workspace/t/a.txt")
        await backend.remove(instance_id, "/workspace/t")
        with pytest.raises(FileNotFound):
            await backend.stat(instance_id, "/workspace/t/s/b.txt")
        await backend.remove(instance_id, "/workspace/t")  # already gone: no error

    async def test_files_do_not_leak_between_instances(self, backend):
        a = await new_instance(backend, "a")
        b = await new_instance(backend, "b")
        await backend.write_stream(a, "/workspace/secret.txt", chunked(b"only a"))
        with pytest.raises(FileNotFound):
            await backend.stat(b, "/workspace/secret.txt")

    async def test_file_calls_on_unknown_instance_raise_instance_gone(self, backend):
        with pytest.raises(InstanceGone):
            await backend.stat("no-such-instance", "/workspace/a")
        with pytest.raises(InstanceGone):
            await backend.list_dir("no-such-instance", "/workspace")
        with pytest.raises(InstanceGone):
            await read_all(backend, "no-such-instance", "/workspace/a")
        with pytest.raises(InstanceGone):
            await backend.write_stream("no-such-instance", "/workspace/a", chunked(b"x"))
        with pytest.raises(InstanceGone):
            await backend.make_dir("no-such-instance", "/workspace/a")
        with pytest.raises(InstanceGone):
            await backend.remove("no-such-instance", "/workspace/a")

    async def test_files_survive_pause_and_resume(self, backend):
        instance_id = await new_instance(backend)
        await backend.write_stream(instance_id, "/workspace/keep.txt", chunked(b"kept"))
        await backend.pause(instance_id)
        with pytest.raises(BackendError) as excinfo:
            await backend.stat(instance_id, "/workspace/keep.txt")
        assert not isinstance(excinfo.value, (InstanceGone, FileNotFound))
        await backend.resume(instance_id)
        assert await read_all(backend, instance_id, "/workspace/keep.txt") == b"kept"
