"""Tests for sandbox file egress/ingress: client file methods, validation, publish/import, tools."""

from __future__ import annotations

import sys
import types
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest

try:  # pragma: no cover - environment dependent
    import datrie  # noqa: F401
except ImportError:  # pragma: no cover
    _stub = types.ModuleType("datrie")
    _stub.Trie = lambda *a, **k: {}
    sys.modules["datrie"] = _stub

from src.integrations.sandbox.client import (
    ArtifactInfo,
    FileDownloadStream,
    SandboxPrincipal,
    SandboxServiceClient,
)
from src.integrations.sandbox.errors import (
    SandboxProtocolError,
    SandboxServiceError,
    SandboxUnavailableError,
)
from src.services import sandbox_file_service as svc

TENANT = str(uuid.uuid4())
WORKSPACE = str(uuid.uuid4())
PRINCIPAL = SandboxPrincipal(TENANT, WORKSPACE, "u1")


def make_client(handler) -> SandboxServiceClient:
    return SandboxServiceClient(
        base_url="http://sandbox",
        api_token="secret-token-1234",
        timeout=httpx.Timeout(5.0),
        limits=httpx.Limits(max_connections=5),
        transport=httpx.MockTransport(handler),
    )


def ok_envelope(data: dict) -> httpx.Response:
    return httpx.Response(200, json={"code": 0, "msg": "OK", "data": data, "error": "", "time": 1})


def error_envelope(status: int, kind: str, code: int = 11023) -> httpx.Response:
    return httpx.Response(
        status,
        json={"code": code, "msg": kind, "data": {"kind": kind, "retryable": False}, "error": "x", "time": 1},
    )


def _patch_settings(monkeypatch, **overrides):
    """The application-service settings are frozen; swap the process-level instance
    for a copy carrying the test overrides (the monolith patched module attributes)."""
    from src import config as app_config

    patched = app_config.current_settings().model_copy(update=overrides)
    monkeypatch.setattr(app_config, "_configured_settings", patched)
    return patched


# --------------------------------------------------------------------------- client


async def test_list_artifacts_parses_items_and_truncation():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = request.read().replace(b" ", b"")
        return ok_envelope(
            {
                "artifacts": [
                    {"aid": "a1", "name": "r.xlsx", "path": "/workspace/sessions/c/out/r.xlsx",
                     "size": 12, "mtime_ms": 5, "content_type": "application/x"},
                    {"aid": "a2", "name": "n.bin", "path": "/workspace/sessions/c/out/n.bin",
                     "size": 1, "mtime_ms": 6},
                ],
                "artifacts_truncated": True,
            }
        )

    client = make_client(handler)
    items, truncated = await client.list_artifacts(PRINCIPAL, "c")
    await client.aclose()

    assert seen["path"] == "/internal/artifacts/list"
    assert b'"conversation_id":"c"' in seen["body"]
    assert truncated is True
    assert [i.aid for i in items] == ["a1", "a2"]
    assert items[1].content_type == "application/octet-stream"


async def test_list_artifacts_malformed_item_is_protocol_error():
    client = make_client(lambda _r: ok_envelope({"artifacts": [{"aid": "a"}]}))
    with pytest.raises(SandboxProtocolError):
        await client.list_artifacts(PRINCIPAL, "c")
    await client.aclose()


async def test_open_artifact_streams_bytes_with_principal_query():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.raw_path.decode()
        seen["params"] = dict(request.url.params)
        return httpx.Response(
            200,
            content=b"hello world",
            headers={"x-file-size": "11", "x-file-mtime-ms": "99", "content-type": "text/plain"},
        )

    client = make_client(handler)
    async with client.open_artifact(PRINCIPAL, "aid/with+chars") as dl:
        data = b"".join([c async for c in dl.chunks])
        assert (dl.size, dl.mtime_ms, dl.content_type) == (11, 99, "text/plain")
    await client.aclose()

    assert data == b"hello world"
    assert seen["path"].startswith("/internal/artifacts/aid%2Fwith%2Bchars")
    assert seen["params"]["end_user_id"] == "u1" and seen["params"]["tenant_id"] == TENANT


async def test_open_artifact_error_status_raises_service_error():
    client = make_client(lambda _r: error_envelope(410, "artifact_gone"))
    with pytest.raises(SandboxServiceError) as info:
        async with client.open_artifact(PRINCIPAL, "a"):
            pytest.fail("must not yield")
    assert info.value.kind == "artifact_gone" and info.value.status_code == 410
    await client.aclose()


async def test_open_artifact_network_error_is_unavailable():
    def handler(_r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    client = make_client(handler)
    with pytest.raises(SandboxUnavailableError):
        async with client.open_artifact(PRINCIPAL, "a"):
            pytest.fail("must not yield")
    await client.aclose()


async def test_upload_file_streams_body_and_returns_size():
    seen: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        seen["body"] = await request.aread()
        return ok_envelope({"path": seen["params"]["path"], "size": len(seen["body"])})

    async def chunks():
        yield b"abc"
        yield b"defg"

    client = make_client(handler)
    size = await client.upload_file(
        PRINCIPAL, "/workspace/sessions/c/in/x.csv", chunks(), overwrite=False
    )
    await client.aclose()

    assert size == 7 and seen["body"] == b"abcdefg"
    assert seen["method"] == "PUT" and seen["path"] == "/internal/files/upload"
    assert seen["params"]["overwrite"] == "false"
    assert seen["params"]["path"] == "/workspace/sessions/c/in/x.csv"


async def test_upload_file_service_error_and_bad_response():
    async def chunks():
        yield b"x"

    client = make_client(lambda _r: error_envelope(409, "file_exists", 11022))
    with pytest.raises(SandboxServiceError) as info:
        await client.upload_file(PRINCIPAL, "/workspace/a", chunks())
    assert info.value.kind == "file_exists"
    await client.aclose()

    client = make_client(lambda _r: ok_envelope({}))
    with pytest.raises(SandboxProtocolError):
        await client.upload_file(PRINCIPAL, "/workspace/a", chunks())
    await client.aclose()


# ------------------------------------------------------------------------ validation


@pytest.mark.parametrize(
    "name, expected",
    [
        ("report.XLSX", None),
        ("a.b.pdf", None),
        ("macro.xlsm", "宏"),
        ("run.exe", "可执行"),
        ("script.sh", "可执行"),
        ("data.parquet", "不支持的文件类型"),
        ("noext", "不支持的文件类型"),
    ],
)
def test_check_extension(name, expected):
    reason = svc.check_extension(name)
    if expected is None:
        assert reason is None
    else:
        assert reason is not None and expected in reason


def test_safe_file_name_strips_paths_and_odd_characters():
    assert svc.safe_file_name("../../etc/passwd") == "passwd"
    assert svc.safe_file_name("a b$c.txt") == "a_b_c.txt"
    assert svc.safe_file_name("季度报告.pptx") == "季度报告.pptx"
    assert svc.safe_file_name("") == "file"
    assert len(svc.safe_file_name("x" * 300)) == 100


async def _collect(gen) -> bytes:
    return b"".join([c async for c in gen])


async def _stream(*parts: bytes):
    for p in parts:
        yield p


async def test_guard_stream_passes_matching_magic_even_when_split():
    holder = svc._Holder()
    out = await _collect(svc._guard_stream(_stream(b"P", b"K\x03\x04rest"), ".docx", holder))
    assert out == b"PK\x03\x04rest" and holder.error is None


async def test_guard_stream_rejects_wrong_or_short_content():
    holder = svc._Holder()
    with pytest.raises(svc.SandboxFileError):
        await _collect(svc._guard_stream(_stream(b"MZ\x90\x00"), ".pptx", holder))
    assert isinstance(holder.error, svc.SandboxFileError)

    holder = svc._Holder()
    with pytest.raises(svc.SandboxFileError):
        await _collect(svc._guard_stream(_stream(b"%P"), ".pdf", holder))


async def test_guard_stream_no_magic_for_plain_types():
    holder = svc._Holder()
    out = await _collect(svc._guard_stream(_stream(b"a,b\n1,2\n"), ".csv", holder))
    assert out == b"a,b\n1,2\n"


def test_describe_error_maps_layers_without_leaking_details():
    gone = SandboxServiceError(status_code=410, code=1, kind="artifact_gone", message="/secret/path")
    assert "重新生成" in svc.describe_error(gone) and "secret" not in svc.describe_error(gone)
    assert svc.describe_error(SandboxUnavailableError("http://10.0.0.1")) == "沙箱服务不可用"
    assert svc.describe_error(svc.SandboxFileError("原因")) == "原因"
    assert svc.describe_error(RuntimeError("boom")) == "文件传输失败"


# ------------------------------------------------------------------- publish / import


class _FakeDb:
    def __init__(self, found=None):
        self.added: list = []
        self.commits = 0
        self._found = found

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def execute(self, _stmt):
        found = self._found
        return SimpleNamespace(scalar_one_or_none=lambda: found)


class _FakeStorageService:
    def __init__(self, *, fail_with: Exception | None = None):
        self.stored: dict[str, bytes] = {}
        self.deleted: list[str] = []
        self.fail_with = fail_with
        self.storage = SimpleNamespace(download_stream=self._download_stream)
        self.source = b""

    async def upload_stream(self, tenant_id, workspace_id, file_id, ext, stream, content_type=None):
        from src.storage.exceptions import StorageUploadError
        from src.services.file_storage_service import generate_file_key

        key = generate_file_key(tenant_id, workspace_id, file_id, ext)
        try:
            data = b"".join([c async for c in stream])
        except Exception as exc:  # real backends wrap stream errors like this
            raise StorageUploadError(message="wrapped", file_key=key, cause=exc) from exc
        if self.fail_with is not None:
            raise self.fail_with
        self.stored[key] = data
        return len(data)

    async def delete_file(self, key):
        self.deleted.append(key)
        return self.stored.pop(key, None) is not None

    async def _download_stream(self, _key):
        yield self.source[:3]
        yield self.source[3:]


class _FakeClient:
    """Serves the artifacts it was given; records uploads."""

    def __init__(self, contents: dict[str, bytes] | None = None, *, cut_at: int | None = None):
        self.contents = contents or {}
        self.cut_at = cut_at
        self.uploaded: list[tuple[str, bytes, bool]] = []
        self.commands: list[str] = []
        self.listed: list[ArtifactInfo] = []
        self.command_exit = 0
        self.files: dict[str, bytes] = {}

    async def read_file(self, _principal, path, *, max_bytes):
        if path not in self.files:
            raise SandboxServiceError(
                status_code=404, code=-1, kind="file_not_found", message="missing"
            )
        data = self.files[path]
        if len(data) > max_bytes:
            raise SandboxServiceError(
                status_code=413, code=-1, kind="file_too_large", message="big"
            )
        return data

    @asynccontextmanager
    async def open_artifact(self, _principal, aid):
        data = self.contents[aid]
        cut = self.cut_at

        async def chunks():
            if cut is not None:
                yield data[:cut]
                raise SandboxUnavailableError("connection dropped")
            yield data

        yield FileDownloadStream(size=len(data), mtime_ms=1, content_type=None, chunks=chunks())

    async def upload_file(self, _principal, path, chunks, *, overwrite=True):
        data = b"".join([c async for c in chunks])
        self.uploaded.append((path, data, overwrite))
        return len(data)

    async def run_command(self, _principal, *, conversation_id, cmd, **_kw):
        self.commands.append(cmd)
        return SimpleNamespace(exit_code=self.command_exit, stderr="no such file", error=None)

    async def list_artifacts(self, _principal, _conversation_id):
        return self.listed, False


@pytest.fixture
def fake_env(monkeypatch):
    db = _FakeDb()
    storage = _FakeStorageService()

    @asynccontextmanager
    async def ctx():
        yield db

    monkeypatch.setattr("src.db.get_async_db_context", ctx)
    monkeypatch.setattr("src.services.file_storage_service.get_file_storage_service", lambda: storage)
    return SimpleNamespace(db=db, storage=storage)


def art(name="r.docx", aid="a1", size=None, path=None, data=b"PK\x03\x04body"):
    return ArtifactInfo(
        aid=aid, name=name, path=path or f"/workspace/sessions/c/out/{name}",
        size=len(data) if size is None else size, mtime_ms=1,
        content_type="application/test",
    ), data


async def test_publish_success_stores_file_and_marks_completed(fake_env):
    info, data = art()
    client = _FakeClient({"a1": data})

    report = await svc.publish_artifacts(client, PRINCIPAL, [info])

    assert report.failed == []
    [done] = report.published
    assert done.size == len(data) and done.url.endswith(f"/storage/permanent/{done.file_id}")
    [meta] = fake_env.db.added
    assert meta.status == "completed" and meta.file_size == len(data)
    assert meta.file_key.startswith(f"{TENANT}/{WORKSPACE}/") and meta.file_key.endswith(".docx")
    assert fake_env.storage.stored[meta.file_key] == data
    assert fake_env.db.commits == 2  # pending, then completed


@pytest.mark.parametrize(
    "name, size, expect",
    [
        ("evil.exe", 4, "可执行"),
        ("m.xlsm", 4, "宏"),
        ("zero.pdf", 0, "为空"),
        ("huge.pdf", 10**12, "上限"),
    ],
)
async def test_publish_rejects_before_touching_storage(fake_env, name, size, expect):
    info, _ = art(name=name, size=size)
    client = _FakeClient({})

    report = await svc.publish_artifacts(client, PRINCIPAL, [info])

    assert report.published == [] and expect in report.failed[0].reason
    assert fake_env.db.added == []  # no FileMetadata for a refused file


async def test_publish_magic_mismatch_marks_failed_and_cleans_up(fake_env):
    info, data = art(name="fake.pptx", data=b"MZ-not-a-zip")
    client = _FakeClient({"a1": data})

    report = await svc.publish_artifacts(client, PRINCIPAL, [info])

    assert report.published == []
    assert "不符" in report.failed[0].reason
    [meta] = fake_env.db.added
    assert meta.status == "failed"
    assert fake_env.storage.deleted == [meta.file_key]
    assert fake_env.storage.stored == {}


async def test_publish_connection_drop_reports_sandbox_unavailable(fake_env):
    info, data = art(name="big.pdf", data=b"%PDF-" + b"x" * 50)
    client = _FakeClient({"a1": data}, cut_at=10)

    report = await svc.publish_artifacts(client, PRINCIPAL, [info])

    assert report.published == []
    assert report.failed[0].reason == "沙箱服务不可用"
    assert fake_env.db.added[0].status == "failed"


async def test_publish_size_mismatch_is_failure(fake_env):
    info, data = art(size=999)  # sandbox listed a different size than it streamed
    client = _FakeClient({"a1": data})

    report = await svc.publish_artifacts(client, PRINCIPAL, [info])

    assert report.published == [] and "字节数" in report.failed[0].reason
    assert fake_env.db.added[0].status == "failed"


async def test_publish_total_limit_skips_the_overflowing_file(fake_env, monkeypatch):
    _patch_settings(monkeypatch, sandbox_file_max_total_bytes=12)
    a, da = art(name="a.txt", aid="a", data=b"123456789")
    b, db_ = art(name="b.txt", aid="b", data=b"123456789")
    client = _FakeClient({"a": da, "b": db_})

    report = await svc.publish_artifacts(client, PRINCIPAL, [a, b])

    assert [p.name for p in report.published] == ["a.txt"]
    assert report.failed[0].name == "b.txt" and "累计" in report.failed[0].reason


async def test_publish_invalid_identity_is_a_failure_not_a_crash(fake_env):
    info, data = art()
    bad = SandboxPrincipal("not-a-uuid", WORKSPACE, "u1")

    report = await svc.publish_artifacts(_FakeClient({"a1": data}), bad, [info])

    assert report.published == [] and "标识无效" in report.failed[0].reason


def _meta(name="data.csv", size=8, key="t/w/f.csv"):
    return SimpleNamespace(file_key=key, file_name=name, file_size=size)


async def test_import_pushes_stream_into_session_in_dir(monkeypatch, fake_env):
    fid = uuid.uuid4()
    fake_env.db._found = _meta(name="my data.csv", size=9)
    fake_env.storage.source = b"a,b\n1,2\n"
    client = _FakeClient()

    out = await svc.import_uploaded_file(
        client, PRINCIPAL, conversation_id="conv-1", file_id=str(fid)
    )

    [(path, data, overwrite)] = client.uploaded
    assert path == f"/workspace/sessions/conv-1/in/{fid}_my_data.csv"
    assert data == b"a,b\n1,2\n" and overwrite is True
    assert out.sandbox_path == path and out.size == len(data)


async def test_import_refuses_unknown_foreign_or_oversized_files(fake_env, monkeypatch):
    client = _FakeClient()
    with pytest.raises(svc.SandboxFileError):  # not found / other workspace
        await svc.import_uploaded_file(
            client, PRINCIPAL, conversation_id="c", file_id=str(uuid.uuid4())
        )
    with pytest.raises(svc.SandboxFileError):  # not a uuid
        await svc.import_uploaded_file(client, PRINCIPAL, conversation_id="c", file_id="nope")

    fake_env.db._found = _meta(size=svc.current_settings().sandbox_file_max_bytes + 1)
    with pytest.raises(svc.SandboxFileError):
        await svc.import_uploaded_file(
            client, PRINCIPAL, conversation_id="c", file_id=str(uuid.uuid4())
        )
    assert client.uploaded == []


async def test_import_maps_missing_object_in_storage(fake_env):
    fake_env.db._found = _meta()

    async def gone(_key):
        raise FileNotFoundError("x")
        yield b""  # pragma: no cover

    fake_env.storage.storage = SimpleNamespace(download_stream=gone)

    class Client(_FakeClient):
        async def upload_file(self, _p, path, chunks, *, overwrite=True):
            async for _ in chunks:
                pass
            return 0

    with pytest.raises(svc.SandboxFileError, match="存储中找不到"):
        await svc.import_uploaded_file(
            Client(), PRINCIPAL, conversation_id="c", file_id=str(uuid.uuid4())
        )


# ------------------------------------------------------------------------------ tools


def _file_tool(monkeypatch, cls, client):
    from src.tools.builtin import sandbox_file_tools as mod
    from src.integrations.sandbox import runtime as rt

    monkeypatch.setattr(rt, "get_sandbox_service_client", lambda: client)

    async def tenant(_ws):
        return TENANT

    monkeypatch.setattr(mod._SandboxFileToolBase, "_resolve_tenant_id", staticmethod(tenant))
    tool = getattr(mod, cls)("tool-x", {})
    tool.set_runtime_context(user_id="u1", workspace_id=WORKSPACE, conversation_id="conv-1")
    return tool


def _published(name="r.xlsx"):
    return svc.PublishReport(
        published=[svc.PublishedFile("fid", name, 10, "application/x", "http://h/storage/permanent/fid")]
    )


async def test_publish_tool_path_in_out_dir_skips_copy(monkeypatch):
    client = _FakeClient()
    out_path = "/workspace/sessions/conv-1/out/r.xlsx"
    client.listed = [art(name="r.xlsx", path=out_path)[0]]
    seen = {}

    async def fake_publish(_c, _p, arts):
        seen["arts"] = arts
        return _published()

    monkeypatch.setattr(svc, "publish_artifacts", fake_publish)
    tool = _file_tool(monkeypatch, "SandboxPublishFileTool", client)

    res = await tool.safe_execute(path=out_path)

    assert res.success and res.data["download_url"].endswith("/fid") and res.data["file_id"] == "fid"
    assert client.commands == [] and [a.path for a in seen["arts"]] == [out_path]


async def test_publish_tool_copies_file_into_out_first(monkeypatch):
    client = _FakeClient()
    target = "/workspace/sessions/conv-1/out/report.pdf"
    client.listed = [art(name="report.pdf", path=target)[0]]

    async def fake_publish(_c, _p, arts):
        return _published("report.pdf")

    monkeypatch.setattr(svc, "publish_artifacts", fake_publish)
    tool = _file_tool(monkeypatch, "SandboxPublishFileTool", client)

    res = await tool.safe_execute(path="/workspace/shared/../shared/report.pdf; rm -rf x")

    # the odd path is normalised, then quoted for the shell, never interpreted
    assert client.commands and "'/workspace/shared/report.pdf; rm -rf x'" in client.commands[0]
    assert not res.success  # the copied name does not match the listed artifact


async def test_publish_tool_copy_failure_and_bad_paths(monkeypatch):
    client = _FakeClient()
    client.command_exit = 1
    tool = _file_tool(monkeypatch, "SandboxPublishFileTool", client)

    res = await tool.safe_execute(path="/workspace/shared/missing.pdf")
    assert not res.success and res.error_code == "SANDBOX_FILE_NOT_FOUND"

    for bad in ("/etc/passwd", "/workspace/../etc/passwd", "../../../etc/passwd"):
        res = await tool.safe_execute(path=bad)
        assert not res.success and res.error_code == "INVALID_ARGUMENT", bad

    # a relative path is resolved against the conversation directory
    client.commands.clear()
    res = await tool.safe_execute(path="gen/report.pdf")
    assert res.error_code == "SANDBOX_FILE_NOT_FOUND"  # copy fails (command_exit=1), but it got that far
    assert "/workspace/sessions/conv-1/gen/report.pdf" in client.commands[0]


async def test_publish_tool_reports_publish_failure(monkeypatch):
    client = _FakeClient()
    out_path = "/workspace/sessions/conv-1/out/x.exe"
    client.listed = [art(name="x.exe", path=out_path)[0]]

    async def fake_publish(_c, _p, arts):
        return svc.PublishReport(failed=[svc.PublishFailure("x.exe", "不支持可执行文件或脚本")])

    monkeypatch.setattr(svc, "publish_artifacts", fake_publish)
    tool = _file_tool(monkeypatch, "SandboxPublishFileTool", client)

    res = await tool.safe_execute(path=out_path)
    assert not res.success and res.error_code == "SANDBOX_PUBLISH_FAILED" and "可执行" in res.error


async def test_file_tools_refuse_missing_identity(monkeypatch):
    client = _FakeClient()
    for cls, args in (("SandboxPublishFileTool", {"path": "/workspace/a.txt"}),
                      ("SandboxImportFileTool", {"file_id": str(uuid.uuid4())}),
                      ("SandboxWriteFileTool", {"path": "a.py", "content": "x"}),
                      ("SandboxReadFileTool", {"path": "a.py"})):
        tool = _file_tool(monkeypatch, cls, client)
        tool.set_runtime_context(user_id="anonymous")
        res = await tool.safe_execute(**args)
        assert not res.success and res.error_code == "SANDBOX_CONTEXT_MISSING"
    assert client.commands == [] and client.uploaded == []


async def test_import_tool_returns_sandbox_path_and_maps_errors(monkeypatch):
    async def fake_import(_c, _p, *, conversation_id, file_id):
        assert conversation_id == "conv-1"
        return svc.ImportedFile(file_id, "d.csv", f"/workspace/sessions/conv-1/in/{file_id}_d.csv", 5)

    monkeypatch.setattr(svc, "import_uploaded_file", fake_import)
    tool = _file_tool(monkeypatch, "SandboxImportFileTool", _FakeClient())
    fid = str(uuid.uuid4())
    res = await tool.safe_execute(file_id=fid)
    assert res.success and res.data["sandbox_path"].endswith(f"{fid}_d.csv")

    async def refuse(*_a, **_k):
        raise svc.SandboxFileError("文件不存在，或不属于当前工作空间")

    monkeypatch.setattr(svc, "import_uploaded_file", refuse)
    res = await tool.safe_execute(file_id=fid)
    assert not res.success and res.error_code == "SANDBOX_IMPORT_FAILED"

    async def down(*_a, **_k):
        raise SandboxUnavailableError("http://10.1.1.1 refused")

    monkeypatch.setattr(svc, "import_uploaded_file", down)
    res = await tool.safe_execute(file_id=fid)
    assert not res.success and res.error_code == "SANDBOX_UNAVAILABLE" and "10.1.1.1" not in res.error


def test_collect_file_ids_accepts_strings_lists_and_file_objects():
    from src.tools.builtin.sandbox_file_tools import _collect_file_ids

    assert _collect_file_ids(" a ") == (["a"], False)
    assert _collect_file_ids(["a", "b", "a", "", None]) == (["a", "b"], True)  # dedup, drop blanks
    objs = [
        {"file_id": "x", "transfer_method": "local_file"},
        {"upload_file_id": "y"},  # no transfer_method: treated as local
        {"file_id": "rand", "transfer_method": "remote_url"},  # random id of a remote file
    ]
    assert _collect_file_ids(objs) == (["x", "y"], True)
    assert _collect_file_ids([]) == ([], True)
    assert _collect_file_ids(None) == ([], False)


def _fake_import_by_id(failing=()):
    async def fake_import(_c, _p, *, conversation_id, file_id):
        if file_id in failing:
            raise svc.SandboxFileError(f"{file_id} 不存在")
        return svc.ImportedFile(file_id, f"{file_id}.csv", f"/workspace/sessions/{conversation_id}/in/{file_id}.csv", 3)

    return fake_import


async def test_import_tool_batch_returns_fixed_structure(monkeypatch):
    monkeypatch.setattr(svc, "import_uploaded_file", _fake_import_by_id())
    tool = _file_tool(monkeypatch, "SandboxImportFileTool", _FakeClient())

    # a list input always gets the batch shape, even with one element
    res = await tool.safe_execute(file_id=["f1"])
    assert res.success and res.data["count"] == 1 and res.data["failed"] == []
    assert [i["file_id"] for i in res.data["imported"]] == ["f1"]

    res = await tool.safe_execute(file_id=["f1", "f2", "f1"])
    assert res.success and res.data["count"] == 2
    assert [i["sandbox_path"].rsplit("/", 1)[-1] for i in res.data["imported"]] == ["f1.csv", "f2.csv"]

    # a plain string keeps the single-file shape
    res = await tool.safe_execute(file_id="f1")
    assert res.success and "imported" not in res.data and res.data["file_id"] == "f1"


async def test_import_tool_batch_partial_failure_is_not_overall_failure(monkeypatch):
    monkeypatch.setattr(svc, "import_uploaded_file", _fake_import_by_id(failing={"bad"}))
    tool = _file_tool(monkeypatch, "SandboxImportFileTool", _FakeClient())

    res = await tool.safe_execute(file_id=["ok", "bad"])
    assert res.success and res.data["count"] == 1
    assert [f["file_id"] for f in res.data["failed"]] == ["bad"]
    assert res.data["failed"][0]["error_code"] == "SANDBOX_IMPORT_FAILED"

    # every file failed -> an error result
    res = await tool.safe_execute(file_id=["bad"])
    assert not res.success and res.error_code == "SANDBOX_IMPORT_FAILED"


async def test_import_tool_batch_stops_when_service_is_down(monkeypatch):
    calls = []

    async def down(_c, _p, *, conversation_id, file_id):
        calls.append(file_id)
        raise SandboxUnavailableError("http://10.1.1.1 refused")

    monkeypatch.setattr(svc, "import_uploaded_file", down)
    tool = _file_tool(monkeypatch, "SandboxImportFileTool", _FakeClient())

    res = await tool.safe_execute(file_id=["a", "b", "c"])
    assert not res.success and res.error_code == "SANDBOX_UNAVAILABLE"
    assert calls == ["a"]  # b and c are not tried
    assert "10.1.1.1" not in res.error


async def test_import_tool_batch_limits_and_remote_only_files(monkeypatch):
    from src.tools.builtin.sandbox_file_tools import _MAX_IMPORT_FILES

    monkeypatch.setattr(svc, "import_uploaded_file", _fake_import_by_id())
    tool = _file_tool(monkeypatch, "SandboxImportFileTool", _FakeClient())

    too_many = [f"f{i}" for i in range(_MAX_IMPORT_FILES + 1)]
    res = await tool.safe_execute(file_id=too_many)
    assert not res.success and res.error_code == "INVALID_ARGUMENT"

    # {{sys.files}} style objects: a remote URL file has no usable id
    res = await tool.safe_execute(file_id=[{"file_id": "rand", "transfer_method": "remote_url"}])
    assert not res.success and res.error_code == "INVALID_ARGUMENT" and "远程" in res.error

    res = await tool.safe_execute(
        file_id=[{"file_id": "x", "transfer_method": "local_file"}, {"file_id": "rand", "transfer_method": "remote_url"}]
    )
    assert res.success and [i["file_id"] for i in res.data["imported"]] == ["x"]


# --------------------------------------------------------------------- local storage


async def test_local_storage_download_stream_roundtrip_and_missing(tmp_path):
    from src.storage.local import LocalStorage

    storage = LocalStorage(str(tmp_path))
    payload = b"0123456789" * 20000  # > one 64KB chunk

    async def src():
        yield payload[:1000]
        yield payload[1000:]

    assert await storage.upload_stream("a/b.bin", src()) == len(payload)
    chunks = [c async for c in storage.download_stream("a/b.bin")]
    assert len(chunks) > 1 and b"".join(chunks) == payload

    with pytest.raises(FileNotFoundError):
        [c async for c in storage.download_stream("nope.bin")]


# ------------------------------------------------------------ import manifest in the prompt


def _manifest(files, tool_names):
    from src.services.draft_run_service import build_sandbox_import_manifest

    tools = [SimpleNamespace(name=n) for n in tool_names]
    return build_sandbox_import_manifest(files, tools)


def test_import_manifest_lists_local_files_only_when_tool_mounted():
    fid = str(uuid.uuid4())
    files = [
        {"type": "document", "transfer_method": "local_file", "upload_file_id": fid, "name": "data.xlsx"},
        {"type": "image", "transfer_method": "remote_url", "url": "http://x/a.png", "name": "a.png"},
    ]
    text = _manifest(files, ["sandbox_import_file", "terminal"])
    assert f"data.xlsx  file_id={fid}" in text
    assert "a.png" not in text  # a remote URL has no file_id

    # without the import tool the model gets no manifest at all
    assert _manifest(files, ["terminal"]) == ""
    assert _manifest(files, []) == ""


def test_import_manifest_empty_cases_and_cap():
    assert _manifest(None, ["sandbox_import_file"]) == ""
    assert _manifest([], ["sandbox_import_file"]) == ""
    remote_only = [{"type": "image", "transfer_method": "remote_url", "url": "http://x/a.png"}]
    assert _manifest(remote_only, ["sandbox_import_file"]) == ""

    many = [
        {"type": "document", "transfer_method": "local_file", "upload_file_id": str(uuid.uuid4()), "name": f"f{i}.csv"}
        for i in range(30)
    ]
    text = _manifest(many, ["sandbox_import_file"])
    assert text.count("file_id=") == 20


def test_import_manifest_includes_history_files_deduped():
    from src.services.draft_run_service import build_sandbox_import_manifest

    tools = [SimpleNamespace(name="sandbox_import_file")]
    old, cur = str(uuid.uuid4()), str(uuid.uuid4())
    current = [{"type": "image", "transfer_method": "local_file", "upload_file_id": cur, "name": "now.png"}]
    # history refs are the shape persisted in messages.meta_data["files"]; the current file repeats
    history = [
        {"type": "image", "transfer_method": "local_file", "upload_file_id": cur, "name": "now.png"},
        {"type": "image", "transfer_method": "local_file", "upload_file_id": old, "name": "cat.jpg"},
        {"type": "image", "transfer_method": "remote_url", "upload_file_id": None, "url": "http://x/a.png"},
    ]
    text = build_sandbox_import_manifest(current, tools, history)
    assert f"cat.jpg  file_id={old}" in text
    assert text.count(f"file_id={cur}") == 1
    assert "a.png" not in text

    # a later turn with no new attachment still gets the earlier file
    assert f"file_id={old}" in build_sandbox_import_manifest(None, tools, history)
    assert build_sandbox_import_manifest(None, [], history) == ""


async def test_manifest_for_turn_skips_db_without_tool_and_survives_lookup_failure(monkeypatch):
    from src.services import draft_run_service as drs

    calls = []

    async def fake_refs(conv_id):
        calls.append(conv_id)
        return [{"type": "image", "transfer_method": "local_file", "upload_file_id": "f-1", "name": "cat.jpg"}]

    monkeypatch.setattr(drs, "load_conversation_file_refs", fake_refs)

    assert await drs.build_sandbox_import_manifest_for_turn(None, [SimpleNamespace(name="terminal")], "c1") == ""
    assert calls == []  # no import tool mounted -> no DB read

    text = await drs.build_sandbox_import_manifest_for_turn(None, [SimpleNamespace(name="sandbox_import_file")], "c1")
    assert "file_id=f-1" in text and calls == ["c1"]

    # no conversation / a failing lookup degrade to "no history", never raise
    monkeypatch.undo()
    assert await drs.load_conversation_file_refs(None) == []


# ------------------------------------------------------------ write text file into the session dir


def test_resolve_session_path_keeps_paths_inside_the_conversation_dir():
    base = "/workspace/sessions/conv-1"
    assert svc.resolve_session_path("conv-1", "gen_ppt.py") == f"{base}/gen_ppt.py"
    assert svc.resolve_session_path("conv-1", "data/../out/r.md") == f"{base}/out/r.md"
    assert svc.resolve_session_path("conv-1", f"{base}/a.txt") == f"{base}/a.txt"

    for bad in ("", "  ", "..", "../x.py", "a/../../x.py", "/etc/passwd", "/workspace/x.py",
                "/workspace/sessions/other/x.py", base, "a\x00b"):
        with pytest.raises(svc.SandboxFileError):
            svc.resolve_session_path("conv-1", bad)


async def test_write_text_file_uploads_utf8_into_the_session_dir():
    client = _FakeClient()

    out = await svc.write_text_file(
        client, PRINCIPAL, conversation_id="conv-1", path="gen_ppt.py", content="print('你好')\n"
    )

    [(path, data, overwrite)] = client.uploaded
    assert path == "/workspace/sessions/conv-1/gen_ppt.py"
    assert data == "print('你好')\n".encode("utf-8") and overwrite is True
    assert out.sandbox_path == path and out.size == len(data)


async def test_write_text_file_rejects_oversized_and_non_string_content():
    client = _FakeClient()
    with pytest.raises(svc.SandboxFileError):
        await svc.write_text_file(
            client, PRINCIPAL, conversation_id="c", path="big.txt",
            content="x" * (svc.MAX_WRITE_BYTES + 1),
        )
    with pytest.raises(svc.SandboxFileError):
        await svc.write_text_file(
            client, PRINCIPAL, conversation_id="c", path="a.txt", content=123  # type: ignore[arg-type]
        )
    assert client.uploaded == []


async def test_write_tool_writes_text_file_with_path_and_content(monkeypatch):
    client = _FakeClient()
    tool = _file_tool(monkeypatch, "SandboxWriteFileTool", client)

    res = await tool.safe_execute(path="scripts/run.py", content="print(1)")

    assert res.success
    assert res.data == {"sandbox_path": "/workspace/sessions/conv-1/scripts/run.py", "size": 8}
    assert client.uploaded[0][0] == "/workspace/sessions/conv-1/scripts/run.py"


async def test_write_tool_refuses_escape_mixed_args_and_missing_half(monkeypatch):
    client = _FakeClient()
    tool = _file_tool(monkeypatch, "SandboxWriteFileTool", client)

    for kwargs in (
        {"path": "../x.py", "content": "x"},
        {"path": "/etc/cron.d/x", "content": "x"},
        {"path": "a.py"},  # content missing
        {"path": "a.py", "content": "x", "old_string": "a", "new_string": "b"},  # both modes
    ):
        res = await tool.safe_execute(**kwargs)
        assert not res.success and res.error_code == "INVALID_ARGUMENT", kwargs

    # a missing required `path` is rejected by the tool base class before execute() runs
    res = await tool.safe_execute(content="x")
    assert not res.success and res.error_code == "VALIDATION_ERROR"
    assert client.uploaded == []


async def test_import_tool_no_longer_writes_text(monkeypatch):
    client = _FakeClient()
    tool = _file_tool(monkeypatch, "SandboxImportFileTool", client)

    res = await tool.safe_execute(path="a.py", content="x")

    assert not res.success  # file_id is required now; path/content are not parameters
    assert client.uploaded == []


async def test_write_tool_maps_service_down_without_leaking_address(monkeypatch):
    class Down(_FakeClient):
        async def upload_file(self, *_a, **_k):
            raise SandboxUnavailableError("http://10.1.1.1 refused")

    tool = _file_tool(monkeypatch, "SandboxWriteFileTool", Down())
    res = await tool.safe_execute(path="a.py", content="x")
    assert not res.success and res.error_code == "SANDBOX_UNAVAILABLE" and "10.1.1.1" not in res.error


# ------------------------------------------------------------ edit a text file in the session dir

SESSION_SCRIPT = "/workspace/sessions/conv-1/gen_ppt.py"


async def test_edit_text_file_replaces_the_single_match_and_writes_back():
    client = _FakeClient()
    client.files[SESSION_SCRIPT] = "a = 1\nb = 2\nc = 3\n".encode("utf-8")

    out = await svc.edit_text_file(
        client, PRINCIPAL, conversation_id="conv-1", path="gen_ppt.py",
        old_string="b = 2", new_string="b = 20",
    )

    [(path, data, overwrite)] = client.uploaded
    assert path == SESSION_SCRIPT and overwrite is True
    assert data == "a = 1\nb = 20\nc = 3\n".encode("utf-8")
    assert out.replaced == 1 and out.size == len(data)


async def test_edit_text_file_empty_new_string_deletes_the_match():
    client = _FakeClient()
    client.files[SESSION_SCRIPT] = b"keep\ndrop\n"

    await svc.edit_text_file(
        client, PRINCIPAL, conversation_id="conv-1", path="gen_ppt.py",
        old_string="drop\n", new_string="",
    )

    assert client.uploaded[0][1] == b"keep\n"


async def test_edit_text_file_refuses_zero_or_many_matches_and_bad_arguments():
    client = _FakeClient()
    client.files[SESSION_SCRIPT] = b"x = 1\nx = 1\ny = 2\n"

    def edit(**kw):
        args = dict(conversation_id="conv-1", path="gen_ppt.py", old_string="y = 2", new_string="y = 3")
        args.update(kw)
        return svc.edit_text_file(client, PRINCIPAL, **args)

    with pytest.raises(svc.SandboxFileError, match="没有找到"):
        await edit(old_string="zzz")
    with pytest.raises(svc.SandboxFileError, match="2 次"):
        await edit(old_string="x = 1")
    with pytest.raises(svc.SandboxFileError):
        await edit(old_string="")
    with pytest.raises(svc.SandboxFileError):
        await edit(old_string="y = 2", new_string="y = 2")
    with pytest.raises(svc.SandboxFileError):
        await edit(old_string=1)  # type: ignore[arg-type]
    for bad in ("../x.py", "/etc/passwd", "/workspace/x.py"):
        with pytest.raises(svc.SandboxFileError):
            await edit(path=bad)
    assert client.uploaded == []


async def test_edit_text_file_rejects_binary_and_oversized_files():
    client = _FakeClient()
    client.files[SESSION_SCRIPT] = b"\xff\xfe\x00bad"
    with pytest.raises(svc.SandboxFileError, match="UTF-8"):
        await svc.edit_text_file(
            client, PRINCIPAL, conversation_id="conv-1", path="gen_ppt.py",
            old_string="a", new_string="b",
        )

    client.files[SESSION_SCRIPT] = b"x" * (svc.MAX_WRITE_BYTES + 1)
    with pytest.raises(SandboxServiceError) as info:  # refused by the size cap before it is read
        await svc.edit_text_file(
            client, PRINCIPAL, conversation_id="conv-1", path="gen_ppt.py",
            old_string="x", new_string="y",
        )
    assert info.value.kind == "file_too_large"

    # the edit itself may not push a file over the cap
    client.files[SESSION_SCRIPT] = b"a" + b"x" * (svc.MAX_WRITE_BYTES - 1)
    with pytest.raises(svc.SandboxFileError, match="上限"):
        await svc.edit_text_file(
            client, PRINCIPAL, conversation_id="conv-1", path="gen_ppt.py",
            old_string="a", new_string="aaaa",
        )
    assert client.uploaded == []


async def test_write_tool_edit_mode_success_and_errors(monkeypatch):
    client = _FakeClient()
    client.files[SESSION_SCRIPT] = b"n = 1\n"
    tool = _file_tool(monkeypatch, "SandboxWriteFileTool", client)

    res = await tool.safe_execute(path="gen_ppt.py", old_string="n = 1", new_string="n = 2")
    assert res.success
    assert res.data == {"sandbox_path": SESSION_SCRIPT, "size": 6, "replaced": 1}
    assert client.uploaded[0][1] == b"n = 2\n"

    client.uploaded.clear()
    for kwargs in (
        {"path": "gen_ppt.py", "old_string": "n = 2", "new_string": "x", "content": "y"},  # mixed with write
        {"path": "gen_ppt.py", "old_string": "nope", "new_string": "x"},  # no match
        {"path": "../x.py", "old_string": "a", "new_string": "b"},  # escape
    ):
        res = await tool.safe_execute(**kwargs)
        assert not res.success and res.error_code == "INVALID_ARGUMENT", kwargs
    assert client.uploaded == []


async def test_write_tool_edit_missing_file_and_service_down(monkeypatch):
    client = _FakeClient()
    tool = _file_tool(monkeypatch, "SandboxWriteFileTool", client)

    res = await tool.safe_execute(path="nope.py", old_string="a", new_string="b")
    assert not res.success and res.error_code == "SANDBOX_FILE_NOT_FOUND"

    class Down(_FakeClient):
        async def read_file(self, *_a, **_k):
            raise SandboxUnavailableError("http://10.1.1.1 refused")

    tool = _file_tool(monkeypatch, "SandboxWriteFileTool", Down())
    res = await tool.safe_execute(path="a.py", old_string="a", new_string="b")
    assert not res.success and res.error_code == "SANDBOX_UNAVAILABLE" and "10.1.1.1" not in res.error


# ----------------------------------------------------------------- sandbox_read_file


async def test_read_text_file_numbers_lines_and_pages():
    client = _FakeClient()
    client.files[SESSION_SCRIPT] = "".join(f"line{i}\n" for i in range(1, 8)).encode()

    first = await svc.read_text_file(
        client, PRINCIPAL, conversation_id="conv-1", path="gen_ppt.py", start_line=1, limit=3
    )
    assert first.content == "1\tline1\n2\tline2\n3\tline3"
    assert (first.start_line, first.end_line, first.total_lines) == (1, 3, 7)
    assert first.truncated is True

    rest = await svc.read_text_file(
        client, PRINCIPAL, conversation_id="conv-1", path="gen_ppt.py", start_line=5, limit=100
    )
    assert rest.content.splitlines()[0] == "5\tline5"
    assert (rest.end_line, rest.truncated) == (7, False)
    assert client.uploaded == []  # read-only


async def test_read_text_file_errors_and_caps():
    client = _FakeClient()
    client.files[SESSION_SCRIPT] = b"a\nb\n"

    for kwargs in ({"start_line": 0}, {"start_line": 9}, {"limit": 0}, {"start_line": True}):
        with pytest.raises(svc.SandboxFileError):
            await svc.read_text_file(
                client, PRINCIPAL, conversation_id="conv-1", path="gen_ppt.py", **kwargs
            )
    with pytest.raises(svc.SandboxFileError):  # escapes the session dir
        await svc.read_text_file(client, PRINCIPAL, conversation_id="conv-1", path="../x.py")

    client.files[SESSION_SCRIPT] = b"\xff\xfe\x00bad"
    with pytest.raises(svc.SandboxFileError, match="UTF-8"):
        await svc.read_text_file(client, PRINCIPAL, conversation_id="conv-1", path="gen_ppt.py")

    client.files[SESSION_SCRIPT] = b"x" * (svc.MAX_READ_BYTES + 1)
    with pytest.raises(SandboxServiceError) as info:
        await svc.read_text_file(client, PRINCIPAL, conversation_id="conv-1", path="gen_ppt.py")
    assert info.value.kind == "file_too_large"

    # long lines are cut and the char budget stops the slice early
    client.files[SESSION_SCRIPT] = (("y" * 5000) + "\n").encode() * 20
    out = await svc.read_text_file(client, PRINCIPAL, conversation_id="conv-1", path="gen_ppt.py")
    assert "行过长已截断" in out.content
    assert len(out.content) <= svc.MAX_READ_CHARS + svc.MAX_LINE_CHARS
    assert out.truncated is True and out.end_line < out.total_lines


async def test_read_tool_success_paging_and_errors(monkeypatch):
    client = _FakeClient()
    client.files[SESSION_SCRIPT] = b"a\nb\nc\n"
    tool = _file_tool(monkeypatch, "SandboxReadFileTool", client)

    res = await tool.safe_execute(path="gen_ppt.py", limit="2")  # string numbers are accepted
    assert res.success
    assert res.data["content"] == "1\ta\n2\tb"
    assert res.data["truncated"] is True and res.data["next_start_line"] == 3

    res = await tool.safe_execute(path="gen_ppt.py", start_line=3, limit="")
    assert res.success and res.data["content"] == "3\tc"
    assert "next_start_line" not in res.data

    # a missing required `path` is rejected by the tool base class before execute() runs
    res = await tool.safe_execute()
    assert not res.success and res.error_code == "VALIDATION_ERROR"

    # a non-numeric start_line is also rejected by the base class parameter validation
    res = await tool.safe_execute(path="gen_ppt.py", start_line="abc")
    assert not res.success and res.error_code == "VALIDATION_ERROR"

    for kwargs in ({"path": "  "}, {"path": "../x"}):
        res = await tool.safe_execute(**kwargs)
        assert not res.success and res.error_code == "INVALID_ARGUMENT", kwargs

    res = await tool.safe_execute(path="nope.py")
    assert not res.success and res.error_code == "SANDBOX_FILE_NOT_FOUND"


async def test_read_tool_service_down_does_not_leak_address(monkeypatch):
    class Down(_FakeClient):
        async def read_file(self, *_a, **_k):
            raise SandboxUnavailableError("http://10.1.1.1 refused")

    tool = _file_tool(monkeypatch, "SandboxReadFileTool", Down())
    res = await tool.safe_execute(path="a.py")
    assert not res.success and res.error_code == "SANDBOX_UNAVAILABLE" and "10.1.1.1" not in res.error


# ----------------------------------------------------------------- default sandbox tools

SANDBOX_TOOLS = [
    "code_interpreter_tool",
    "terminal_tool",
    "sandbox_read_file",
    "sandbox_write_file",
    "sandbox_import_file",
    "sandbox_publish_file",
]


def test_default_injection_is_empty_when_the_switch_is_off(monkeypatch):
    from src.tools.builtin import sandbox_default as sd

    monkeypatch.setattr(sd, "is_sandbox_enabled", lambda: False)
    assert sd.build_sandbox_tools(user_id="u1", workspace_id=WORKSPACE) == []


def test_default_injection_builds_every_sandbox_tool_with_runtime_context(monkeypatch):
    from src.tools.builtin import sandbox_default as sd

    monkeypatch.setattr(sd, "is_sandbox_enabled", lambda: True)

    tools = sd.build_sandbox_tools(user_id="u1", workspace_id=WORKSPACE, conversation_id="conv-1")

    assert [t.name for t in tools] == SANDBOX_TOOLS
    assert set(sd.SANDBOX_TOOL_NAMES) == set(SANDBOX_TOOLS)
    for t in tools:
        assert t.tool_instance.operation == t.name  # recorded for draft_run_service serialization
        assert t.tool_instance.get_runtime_context("user_id") == "u1"
        assert t.tool_instance.get_runtime_context("workspace_id") == WORKSPACE
        assert t.tool_instance.get_runtime_context("conversation_id") == "conv-1"
        assert "operation" not in t.args_schema.model_fields


def test_default_injection_instances_do_not_share_state_between_runs(monkeypatch):
    from src.tools.builtin import sandbox_default as sd

    monkeypatch.setattr(sd, "is_sandbox_enabled", lambda: True)
    first = sd.build_sandbox_tools(user_id="u1", workspace_id=WORKSPACE, conversation_id="c1")
    second = sd.build_sandbox_tools(user_id="u2", workspace_id=WORKSPACE, conversation_id="c2")

    assert first[0].tool_instance is not second[0].tool_instance
    assert first[0].tool_instance.get_runtime_context("conversation_id") == "c1"


def test_drop_sandbox_tools_removes_stale_configured_copies_only(monkeypatch):
    from src.tools.builtin import sandbox_default as sd

    monkeypatch.setattr(sd, "is_sandbox_enabled", lambda: True)
    stale = sd.build_sandbox_tools(user_id="u1", workspace_id=WORKSPACE)
    other = SimpleNamespace(name="datetime_tool")

    kept = sd.drop_sandbox_tools([other, *stale])

    assert kept == [other]


def test_sandbox_rows_in_the_tool_library_are_all_hidden():
    from src.services.tool_service import ToolService

    def row(tool_class, tool_type="builtin"):
        return SimpleNamespace(tool_type=tool_type, config_data={"tool_class": tool_class})

    for legacy in (
        "SandboxToolkit", "CodeInterpreterTool", "TerminalTool", "SandboxPublishFileTool",
        "SandboxImportFileTool", "SandboxReadFileTool", "SandboxWriteFileTool",
    ):
        assert ToolService._is_legacy_sandbox_row(row(legacy)) is True, legacy
    assert ToolService._is_legacy_sandbox_row(row("DateTimeTool")) is False
    assert ToolService._is_legacy_sandbox_row(row("TerminalTool", tool_type="custom")) is False




# ------------------------------------------------- prompt: publish without any way to create files


def test_methodology_does_not_promise_delivery_when_nothing_can_create_files():
    from src.agent.langchain_agent import build_agent_methodology

    text = build_agent_methodology(["sandbox_publish_file"])

    assert "没有任何能在沙箱里生成文件的工具" in text
    assert "不要编造文件路径" in text
    # the "after generating the deliverable, call publish" instruction must not appear
    assert "生成了要交给用户的成品文件" not in text


def test_methodology_keeps_delivery_instruction_when_a_file_creating_tool_is_assigned():
    from src.agent.langchain_agent import build_agent_methodology

    for creator in ("terminal_tool", "code_interpreter_tool", "sandbox_write_file"):
        text = build_agent_methodology([creator, "sandbox_publish_file"])
        assert "生成了要交给用户的成品文件" in text, creator
        assert "没有任何能在沙箱里生成文件的工具" not in text, creator

