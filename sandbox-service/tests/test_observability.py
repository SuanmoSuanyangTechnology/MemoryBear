from __future__ import annotations

import asyncio
import io
import json
import threading

import httpx

from src.api import create_app
from src.backends import FakeBackend
from src.clock import ManualClock
from src.egress import StrictEgressPolicy
from src.keys import KernelKey, SandboxKey
from src.observability import (
    AsyncOperationAuditSink,
    AuditConfig,
    InMemoryOperationAuditSink,
    JsonlOperationAuditSink,
    NullOperationAuditSink,
    OperationAuditEvent,
    StdoutOperationAuditSink,
    build_audit_sink,
)
from src.services import SandboxManager, SchedulerConfig

TOKEN = "unit-test-token-0123456789"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
WHO = {"tenant_id": "tenant", "workspace_id": "workspace", "end_user_id": "user"}


def make_observed_app():
    clock = ManualClock()
    backend = FakeBackend(clock)
    sink = InMemoryOperationAuditSink()
    manager = SandboxManager(
        backend,
        SchedulerConfig(),
        clock=clock,
        audit_sink=sink,
    )
    app = create_app(manager, api_token=TOKEN)
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://sandbox",
    )
    return backend, manager, sink, client


def test_jsonl_sink_appends_compact_flushed_records(tmp_path):
    target = tmp_path / "audit" / "sandbox.jsonl"
    sink = JsonlOperationAuditSink(target)
    sink.record(
        OperationAuditEvent(
            ts_ms=123,
            operation="execute",
            outcome="ok",
            tenant_id="tenant",
            details={"duration_ms": 7},
        )
    )

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload == {
        "ts_ms": 123,
        "operation": "execute",
        "outcome": "ok",
        "tenant_id": "tenant",
        "details": {"duration_ms": 7},
    }
    sink.close()
    sink.close()


async def test_metrics_require_bearer_and_use_prometheus_content_type():
    _backend, _manager, _sink, client = make_observed_app()
    async with client:
        denied = await client.get("/internal/metrics")
        response = await client.get("/internal/metrics", headers=AUTH)

    assert denied.status_code == 401
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/plain; version=0.0.4; charset=utf-8"
    assert "# TYPE sandbox_instances gauge" in response.text
    assert 'sandbox_instances{state="running"} 0' in response.text
    assert "tenant" not in response.text
    assert "workspace" not in response.text
    assert "user" not in response.text


async def test_operations_are_audited_without_request_contents():
    _backend, manager, sink, client = make_observed_app()
    secret = "do-not-write-this-secret"
    execute = {**WHO, "conversation_id": "conversation", "code": f"print('{secret}')"}
    command = {**WHO, "conversation_id": "conversation", "cmd": f"echo {secret}"}

    async with client:
        execute_response = await client.post("/internal/execute", json=execute, headers=AUTH)
        command_response = await client.post("/internal/commands", json=command, headers=AUTH)
        assert execute_response.status_code == 200
        assert command_response.status_code == 200
        assert (
            await client.put(
                "/internal/files/upload",
                params={**WHO, "path": "/workspace/secret.txt"},
                content=secret,
                headers=AUTH,
            )
        ).status_code == 200
        assert (
            await client.post(
                "/internal/close-conversation",
                json={**WHO, "conversation_id": "conversation"},
                headers=AUTH,
            )
        ).status_code == 200
        assert (
            await client.post("/internal/destroy-user", json=WHO, headers=AUTH)
        ).status_code == 200
        metrics = await client.get("/internal/metrics", headers=AUTH)

    operations = {(event.operation, event.outcome) for event in sink.events}
    assert ("execute", "ok") in operations
    assert any(operation == "commands" for operation, _outcome in operations)
    assert ("files_upload", "success") in operations
    assert ("close_conversation", "success") in operations
    assert ("destroy_user", "success") in operations
    assert any(
        event.operation == "lifecycle" and event.details == {"action": "created"}
        for event in sink.events
    )
    serialized = "\n".join(json.dumps(event.to_dict()) for event in sink.events)
    assert secret not in serialized
    assert "Authorization" not in serialized
    assert 'sandbox_operations_total{operation="execute",outcome="ok"} 1' in metrics.text
    assert manager.metrics["operation.execute.ok"] == 1


async def test_strict_egress_is_applied_audited_and_exported():
    clock = ManualClock()
    backend = FakeBackend(clock)
    sink = InMemoryOperationAuditSink()
    policy = StrictEgressPolicy(("pypi.org",), "policy-fingerprint")
    manager = SandboxManager(
        backend,
        SchedulerConfig(),
        clock=clock,
        audit_sink=sink,
        egress_policy=policy,
    )
    app = create_app(manager, api_token=TOKEN)

    outcome = await manager.execute(
        KernelKey(SandboxKey(**WHO), "conversation"),
        "print(1)",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://sandbox"
    ) as client:
        metrics = await client.get("/internal/metrics", headers=AUTH)

    assert backend.network_of(outcome.instance_id) == (False, policy.network_config())
    applied = [event for event in sink.events if event.operation == "egress_policy"]
    assert len(applied) == 1
    assert applied[0].outcome == "applied"
    assert applied[0].details == {
        "mode": "strict",
        "fingerprint": "policy-fingerprint",
        "rule_count": 2,
    }
    assert "sandbox_egress_policy_applied_total 1" in metrics.text
    assert 'sandbox_operations_total{operation="egress_policy",outcome="applied"} 1' in metrics.text


async def test_download_is_audited_after_stream_completes():
    _backend, _manager, sink, client = make_observed_app()

    async with client:
        uploaded = await client.put(
            "/internal/files/upload",
            params={**WHO, "path": "/workspace/report.pdf"},
            content=b"document",
            headers=AUTH,
        )
        assert uploaded.status_code == 200
        response = await client.get(
            "/internal/files/download",
            params={**WHO, "path": "/workspace/report.pdf"},
            headers=AUTH,
        )

    assert response.status_code == 200
    assert response.content == b"document"
    downloads = [event for event in sink.events if event.operation == "files_download"]
    assert len(downloads) == 1
    assert downloads[0].outcome == "success"
    assert downloads[0].details == {"size": 8}


async def test_error_path_and_backend_failure_are_counted_once():
    backend, manager, sink, client = make_observed_app()
    backend.fail["create"] = 1
    body = {**WHO, "conversation_id": "conversation", "code": "x"}

    async with client:
        failed = await client.post("/internal/execute", json=body, headers=AUTH)
        metrics = await client.get("/internal/metrics", headers=AUTH)

    assert failed.status_code == 503
    assert manager.metrics["backend_error.create.BackendUnavailable"] == 1
    assert manager.metrics["operation.execute.error"] == 1
    assert sum(event.operation == "backend_create" for event in sink.events) == 1
    assert (
        sum(event.operation == "execute" and event.outcome == "error" for event in sink.events) == 1
    )
    assert (
        'sandbox_backend_errors_total{kind="BackendUnavailable",operation="create"} 1'
        in metrics.text
    )


async def test_destroy_user_backend_failure_is_structured_and_counted_once():
    backend, manager, sink, client = make_observed_app()
    body = {**WHO, "conversation_id": "conversation", "code": "x"}

    async with client:
        assert (await client.post("/internal/execute", json=body, headers=AUTH)).status_code == 200
        backend.fail["destroy"] = 1
        failed = await client.post("/internal/destroy-user", json=WHO, headers=AUTH)
        metrics = await client.get("/internal/metrics", headers=AUTH)

    assert failed.status_code == 503
    payload = failed.json()
    assert payload["msg"] == "backend_unavailable"
    assert payload["data"]["retryable"] is True
    assert manager.registry.get(SandboxKey(**WHO).digest) is not None
    assert manager.metrics["backend_error.destroy_user.BackendUnavailable"] == 1
    assert manager.metrics["operation.destroy_user.error"] == 1
    assert sum(event.operation == "backend_destroy_user" for event in sink.events) == 1
    assert (
        'sandbox_backend_errors_total{kind="BackendUnavailable",operation="destroy_user"} 1'
        in metrics.text
    )


async def test_audit_sink_failure_does_not_interrupt_execution():
    class BrokenSink:
        def record(self, _event):
            raise OSError("disk full")

        def close(self):
            pass

    clock = ManualClock()
    manager = SandboxManager(
        FakeBackend(clock),
        SchedulerConfig(),
        clock=clock,
        audit_sink=BrokenSink(),
    )
    key = KernelKey(SandboxKey("tenant", "workspace", "user"), "conversation")

    result = await manager.execute(key, "print(1)")

    assert result.verdict.ok
    assert manager.metrics["audit_write_failures"] >= 1


def test_stdout_sink_writes_one_json_object_per_line():
    stream = io.StringIO()
    sink = StdoutOperationAuditSink(stream)

    sink.record(OperationAuditEvent(ts_ms=1, operation="execute", outcome="ok", tenant_id="t"))
    sink.record(OperationAuditEvent(ts_ms=2, operation="commands", outcome="error"))
    sink.close()

    lines = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert lines[0] == {"ts_ms": 1, "operation": "execute", "outcome": "ok", "tenant_id": "t"}
    assert lines[1] == {"ts_ms": 2, "operation": "commands", "outcome": "error"}


def test_jsonl_sink_rotates_and_keeps_bounded_backups(tmp_path):
    target = tmp_path / "audit.jsonl"
    sink = JsonlOperationAuditSink(target, max_bytes=120, backup_count=2)

    for index in range(20):
        sink.record(
            OperationAuditEvent(ts_ms=index, operation="execute", outcome="ok", tenant_id="t")
        )
    sink.close()

    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["audit.jsonl", "audit.jsonl.1", "audit.jsonl.2"]
    # Rotation keeps at most backup_count older files, each holding only whole records.
    for name in names:
        path = tmp_path / name
        for line in path.read_text(encoding="utf-8").splitlines():
            assert json.loads(line)["operation"] == "execute"
    # The newest record survives and older ones are bounded, not silently truncated mid-line.
    assert "ts_ms" in (tmp_path / "audit.jsonl.1").read_text(encoding="utf-8")


def test_async_sink_writes_off_the_caller_thread_and_drains_on_close():
    captured: list[OperationAuditEvent] = []

    class SlowSink:
        def record(self, event):
            captured.append(event)

        def close(self):
            pass

    sink = AsyncOperationAuditSink(SlowSink(), queue_size=100)
    for index in range(50):
        sink.record(OperationAuditEvent(ts_ms=index, operation="execute", outcome="ok"))
    sink.close()

    assert [event.ts_ms for event in captured] == list(range(50))


def test_async_sink_counts_dropped_events_instead_of_blocking():
    class BlockedSink:
        def __init__(self):
            self.started = threading.Event()
            self.release = threading.Event()

        def record(self, event):
            self.started.set()
            self.release.wait(5)

        def close(self):
            self.release.set()

    inner = BlockedSink()
    sink = AsyncOperationAuditSink(inner, queue_size=1)
    sink.record(OperationAuditEvent(ts_ms=1, operation="execute", outcome="ok"))
    inner.started.wait(2)
    for index in range(5):
        sink.record(OperationAuditEvent(ts_ms=index, operation="execute", outcome="ok"))

    counters = sink.counters()
    inner.release.set()
    sink.close()

    assert counters["dropped"] >= 1


def test_async_sink_counts_write_failures_without_raising():
    class BrokenSink:
        def record(self, _event):
            raise OSError("disk full")

        def close(self):
            pass

    sink = AsyncOperationAuditSink(BrokenSink(), queue_size=10)
    sink.record(OperationAuditEvent(ts_ms=1, operation="execute", outcome="ok"))
    sink.close()

    assert sink.counters()["failures"] == 1


def test_build_audit_sink_selects_mode_from_config():
    assert isinstance(build_audit_sink(AuditConfig(audit_sink="none")), NullOperationAuditSink)

    stdout_sink = build_audit_sink(AuditConfig(audit_sink="stdout"))
    assert isinstance(stdout_sink, AsyncOperationAuditSink)
    stdout_sink.close()


def test_build_audit_sink_file_mode_creates_the_file(tmp_path):
    path = tmp_path / "nested" / "audit.jsonl"
    sink = build_audit_sink(AuditConfig(audit_sink="file", audit_log_path=str(path)))
    sink.record(OperationAuditEvent(ts_ms=7, operation="execute", outcome="ok"))
    sink.close()

    assert json.loads(path.read_text(encoding="utf-8").splitlines()[0])["ts_ms"] == 7


async def test_async_sink_counters_surface_as_metrics():
    clock = ManualClock()

    class BrokenSink:
        def record(self, _event):
            raise OSError("disk full")

        def close(self):
            pass

    manager = SandboxManager(
        FakeBackend(clock),
        SchedulerConfig(),
        clock=clock,
        audit_sink=AsyncOperationAuditSink(BrokenSink(), queue_size=10),
    )
    app = create_app(manager, api_token=TOKEN)
    key = KernelKey(SandboxKey("tenant", "workspace", "user"), "conversation")

    result = await manager.execute(key, "print(1)")
    # The drain thread has to observe the failure before /metrics is scraped.
    for _ in range(100):
        if manager.stats()["metrics"].get("audit_write_failures"):
            break
        await asyncio.sleep(0.01)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://sandbox"
    ) as client:
        metrics = await client.get("/internal/metrics", headers=AUTH)
    manager._audit_sink.close()  # noqa: SLF001 - close the writer thread for the test

    assert result.verdict.ok
    assert "sandbox_audit_write_failures_total " in metrics.text
    assert "sandbox_audit_dropped_total 0" in metrics.text
    failures_line = next(
        line
        for line in metrics.text.splitlines()
        if line.startswith("sandbox_audit_write_failures_total ")
    )
    assert int(failures_line.split()[-1]) >= 1
