"""File flow: path policy, artifact ids, manager file operations and the HTTP routes."""

from __future__ import annotations

import httpx
import pytest

from src.api import create_app
from src.backends import FakeBackend
from src.clock import ManualClock
from src.errors import SandboxError
from src.files.artifact import ArtifactClaims, ArtifactSigner, content_type_for
from src.files.config import FilesConfig
from src.files.paths import normalize, out_dir
from src.keys import SandboxKey
from src.services import SandboxManager, SchedulerConfig

TOKEN = "unit-test-token-0123456789"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
WHO = {"tenant_id": "t", "workspace_id": "w", "end_user_id": "u1"}
OUT = "/workspace/sessions/c1/out"


def sbx(user: str = "u1") -> SandboxKey:
    return SandboxKey("t", "w", user)


async def chunks(data: bytes, size: int = 7):
    for i in range(0, len(data), size):
        yield data[i : i + size]


def make_manager(**files) -> tuple[SandboxManager, FakeBackend, ManualClock]:
    clock = ManualClock()
    backend = FakeBackend(clock)
    mgr = SandboxManager(backend, SchedulerConfig(), clock=clock, files=FilesConfig(**files))
    return mgr, backend, clock


async def read(mgr: SandboxManager, path: str, user: str = "u1") -> bytes:
    dl = await mgr.open_download(sbx(user), path)
    return b"".join([c async for c in dl.stream()])


def busy(mgr: SandboxManager, user: str = "u1") -> int:
    return mgr.registry.get(sbx(user).digest).busy


# -- paths ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/workspace", "/workspace"),
        ("/workspace/a/../b", "/workspace/b"),
        ("/workspace//x/./y/", "/workspace/x/y"),
        ("//workspace/a", "/workspace/a"),
    ],
)
def test_normalize_accepts_and_canonicalises(raw, expected):
    assert normalize(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["/workspace/../etc", "/etc/passwd", "relative/x", "/workspacex", "/", "/workspace/a\x00b"],
)
def test_normalize_rejects_paths_outside_workspace(raw):
    with pytest.raises(SandboxError) as exc:
        normalize(raw)
    assert exc.value.kind == "path_forbidden"


@pytest.mark.parametrize("raw", ["", "   ", None, 5])
def test_normalize_requires_a_string(raw):
    with pytest.raises(SandboxError) as exc:
        normalize(raw)
    assert exc.value.kind == "invalid_request"


@pytest.mark.parametrize("conv", ["", "a/b", "..", "a\\b"])
def test_out_dir_rejects_unusable_conversation_ids(conv):
    with pytest.raises(SandboxError):
        out_dir(conv)


# -- artifact ids ---------------------------------------------------------------------


def claims(**over):
    base = {"digest": "d", "path": f"{OUT}/r.xlsx", "size": 3, "mtime_ms": 9, "expires_at": 2000}
    base.update(over)
    return ArtifactClaims(**base)


def test_aid_round_trip():
    signer = ArtifactSigner("k" * 20)
    assert signer.verify(signer.issue(claims()), now_s=1000) == claims()


def test_aid_rejects_tampering_expiry_other_key_and_garbage():
    signer = ArtifactSigner("k" * 20)
    aid = signer.issue(claims())
    body, mac = aid.split(".")
    for bad in [f"{body}x.{mac}", f"{body}.{mac[:-2]}ab", "nodot", "", "a.b.c", f"{body}."]:
        with pytest.raises(SandboxError) as exc:
            signer.verify(bad, now_s=1000)
        assert exc.value.kind == "artifact_gone"
    with pytest.raises(SandboxError):
        signer.verify(aid, now_s=2000)  # expired exactly now
    with pytest.raises(SandboxError):
        ArtifactSigner("z" * 20).verify(aid, now_s=1000)


def test_content_type_table_does_not_depend_on_the_system():
    assert content_type_for("a.PPTX").endswith("presentationml.presentation")
    assert content_type_for("x.csv") == "text/csv"
    assert content_type_for("x.unknown") == "application/octet-stream"
    assert content_type_for("noext") == "application/octet-stream"


def test_files_config_validation():
    with pytest.raises(ValueError):
        FilesConfig(artifact_key="short")
    with pytest.raises(ValueError):
        FilesConfig(artifact_ttl_s=0)


# -- manager: uploads, downloads, listing -----------------------------------------------


async def test_reads_never_create_an_instance():
    mgr, backend, _ = make_manager()
    with pytest.raises(SandboxError) as exc:
        await mgr.files_stat(sbx(), "/workspace/a")
    assert exc.value.kind == "file_not_found"
    with pytest.raises(SandboxError):
        await mgr.open_download(sbx(), "/workspace/a")
    assert await mgr.artifacts_list(sbx(), "c1") == ([], False)
    await mgr.files_delete(sbx(), "/workspace/a")  # nothing to delete is fine
    assert backend.created_total == 0


async def test_upload_allocates_and_download_returns_the_bytes():
    mgr, backend, _ = make_manager()
    data = bytes(range(256)) * 3
    uploaded = await mgr.files_upload(sbx(), "/workspace/sessions/c1/in/d.bin", chunks(data))
    assert uploaded == len(data)
    assert backend.created_total == 1
    assert await read(mgr, "/workspace/sessions/c1/in/d.bin") == data
    assert busy(mgr) == 0
    entry = await mgr.files_stat(sbx(), "/workspace/sessions/c1/in/d.bin")
    assert entry.size == len(data)


async def test_upload_refuses_existing_target_unless_overwrite():
    mgr, _, _ = make_manager()
    await mgr.files_upload(sbx(), "/workspace/a.txt", chunks(b"one"))
    with pytest.raises(SandboxError) as exc:
        await mgr.files_upload(sbx(), "/workspace/a.txt", chunks(b"two"))
    assert exc.value.kind == "file_exists"
    assert await read(mgr, "/workspace/a.txt") == b"one"
    await mgr.files_upload(sbx(), "/workspace/a.txt", chunks(b"two"), overwrite=True)
    assert await read(mgr, "/workspace/a.txt") == b"two"


async def test_upload_over_the_limit_fails_and_leaves_nothing_behind():
    mgr, _, _ = make_manager(max_file_bytes=10)
    with pytest.raises(SandboxError) as exc:
        await mgr.files_upload(sbx(), "/workspace/big.bin", chunks(b"x" * 11))
    assert exc.value.kind == "file_too_large"
    with pytest.raises(SandboxError) as gone:
        await mgr.files_stat(sbx(), "/workspace/big.bin")
    assert gone.value.kind == "file_not_found"
    assert busy(mgr) == 0


async def test_download_over_the_limit_is_refused():
    mgr, backend, _ = make_manager(max_file_bytes=10)
    await mgr.files_upload(sbx(), "/workspace/ok.bin", chunks(b"x" * 10))
    iid = mgr.registry.get(sbx().digest).instance_id
    backend.put_file(iid, "/workspace/huge.bin", b"y" * 11)
    with pytest.raises(SandboxError) as exc:
        await mgr.open_download(sbx(), "/workspace/huge.bin")
    assert exc.value.kind == "file_too_large"
    assert busy(mgr) == 0


async def test_paths_outside_workspace_are_forbidden_everywhere():
    mgr, backend, _ = make_manager()
    for call in (
        mgr.files_stat(sbx(), "/etc/passwd"),
        mgr.files_list(sbx(), "/workspace/../etc"),
        mgr.files_delete(sbx(), "/workspace"),
        mgr.files_upload(sbx(), "/tmp/x", chunks(b"x")),
        mgr.open_download(sbx(), "/root/.ssh/id_rsa"),
    ):
        with pytest.raises(SandboxError) as exc:
            await call
        assert exc.value.kind == "path_forbidden"
    assert backend.created_total == 0


async def test_downloading_a_directory_is_an_invalid_request():
    mgr, _, _ = make_manager()
    await mgr.files_upload(sbx(), "/workspace/d/a.txt", chunks(b"a"))
    with pytest.raises(SandboxError) as exc:
        await mgr.open_download(sbx(), "/workspace/d")
    assert exc.value.kind == "invalid_request"
    assert busy(mgr) == 0


async def test_list_and_delete():
    mgr, _, _ = make_manager()
    await mgr.files_upload(sbx(), "/workspace/d/a.txt", chunks(b"a"))
    await mgr.files_upload(sbx(), "/workspace/d/s/b.txt", chunks(b"bb"))
    entries, truncated = await mgr.files_list(sbx(), "/workspace/d", 1)
    assert [e.name for e in entries] == ["a.txt", "s"] and not truncated
    deep, _ = await mgr.files_list(sbx(), "/workspace/d", 2)
    assert "/workspace/d/s/b.txt" in {e.path for e in deep}
    with pytest.raises(SandboxError):
        await mgr.files_list(sbx(), "/workspace/d", 99)
    await mgr.files_delete(sbx(), "/workspace/d")
    with pytest.raises(SandboxError) as exc:
        await mgr.files_stat(sbx(), "/workspace/d/a.txt")
    assert exc.value.kind == "file_not_found"


async def test_users_do_not_see_each_others_files():
    mgr, _, _ = make_manager()
    await mgr.files_upload(sbx("u1"), "/workspace/secret.txt", chunks(b"mine"))
    await mgr.files_upload(sbx("u2"), "/workspace/other.txt", chunks(b"x"))
    with pytest.raises(SandboxError) as exc:
        await mgr.files_stat(sbx("u2"), "/workspace/secret.txt")
    assert exc.value.kind == "file_not_found"


async def test_files_on_a_paused_instance_resume_it():
    mgr, backend, clock = make_manager()
    await mgr.files_upload(sbx(), "/workspace/keep.txt", chunks(b"kept"))
    clock.advance(21 * 60)
    assert (await mgr.tick()).paused == 1
    assert await read(mgr, "/workspace/keep.txt") == b"kept"
    assert backend.resumed_total == 1


async def test_a_lost_instance_is_reported_as_instance_lost():
    mgr, backend, _ = make_manager()
    await mgr.files_upload(sbx(), "/workspace/a.txt", chunks(b"a"))
    backend.lose_instance(mgr.registry.get(sbx().digest).instance_id)
    with pytest.raises(SandboxError) as exc:
        await mgr.files_stat(sbx(), "/workspace/a.txt")
    assert exc.value.kind == "instance_lost"


# -- manager: artifacts -----------------------------------------------------------------


async def make_artifact(mgr, name="report.xlsx", data=b"PK-data"):
    await mgr.files_upload(sbx(), f"{OUT}/{name}", chunks(data))
    listed, _ = await mgr.artifacts_list(sbx(), "c1")
    return listed[0]


async def test_artifacts_list_returns_signed_entries_with_content_type():
    mgr, _, _ = make_manager()
    await mgr.files_upload(sbx(), "/workspace/sessions/c1/in/not-mine.txt", chunks(b"x"))
    art = await make_artifact(mgr)
    listed, truncated = await mgr.artifacts_list(sbx(), "c1")
    assert len(listed) == 1 and not truncated  # only out/ is scanned
    assert (art["name"], art["size"], art["path"]) == ("report.xlsx", 7, f"{OUT}/report.xlsx")
    assert art["content_type"].endswith("spreadsheetml.sheet")
    assert art["mtime_ms"] > 0 and art["aid"]
    assert await mgr.artifacts_list(sbx(), "other-conv") == ([], False)


async def test_artifacts_list_is_capped():
    mgr, _, _ = make_manager(max_artifacts=2)
    for i in range(3):
        await mgr.files_upload(sbx(), f"{OUT}/f{i}.txt", chunks(b"x"))
    listed, truncated = await mgr.artifacts_list(sbx(), "c1")
    assert len(listed) == 2 and truncated


async def test_artifact_download_round_trip():
    mgr, _, _ = make_manager()
    art = await make_artifact(mgr)
    claims_, dl = await mgr.open_artifact(sbx(), art["aid"])
    assert claims_.path == art["path"]
    assert b"".join([c async for c in dl.stream()]) == b"PK-data"
    assert busy(mgr) == 0


async def test_artifact_changed_after_listing_is_gone():
    mgr, _, clock = make_manager()
    art = await make_artifact(mgr)
    clock.advance(5)
    await mgr.files_upload(sbx(), art["path"], chunks(b"different"), overwrite=True)
    with pytest.raises(SandboxError) as exc:
        await mgr.open_artifact(sbx(), art["aid"])
    assert exc.value.kind == "artifact_gone"
    assert busy(mgr) == 0


async def test_artifact_expires_and_binds_to_its_user():
    mgr, _, clock = make_manager(artifact_ttl_s=60)
    art = await make_artifact(mgr)
    await mgr.files_upload(sbx("u2"), "/workspace/x", chunks(b"x"))
    with pytest.raises(SandboxError) as exc:
        await mgr.open_artifact(sbx("u2"), art["aid"])
    assert exc.value.kind == "unauthorized"
    clock.advance(61)
    with pytest.raises(SandboxError) as gone:
        await mgr.open_artifact(sbx(), art["aid"])
    assert gone.value.kind == "artifact_gone"


async def test_artifact_of_a_destroyed_instance_is_gone():
    mgr, _, _ = make_manager()
    art = await make_artifact(mgr)
    await mgr.destroy_user(sbx())
    with pytest.raises(SandboxError) as exc:
        await mgr.open_artifact(sbx(), art["aid"])
    assert exc.value.kind == "artifact_gone"


async def test_an_open_download_keeps_the_instance_from_being_paused():
    mgr, _, clock = make_manager()
    art = await make_artifact(mgr)
    _, dl = await mgr.open_artifact(sbx(), art["aid"])
    assert busy(mgr) == 1
    clock.advance(21 * 60)
    assert (await mgr.tick()).paused == 0
    await dl.aclose()
    await dl.aclose()  # idempotent
    assert busy(mgr) == 0


# -- HTTP routes ------------------------------------------------------------------------


@pytest.fixture
def api():
    mgr, backend, clock = make_manager()
    app = create_app(mgr, api_token=TOKEN)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://sandbox")
    return mgr, backend, client


async def put(client, path, data, **extra):
    return await client.put(
        "/internal/files/upload", params={**WHO, "path": path, **extra}, content=data, headers=AUTH
    )


async def test_http_upload_download_and_stat(api):
    _mgr, _backend, client = api
    async with client:
        data = bytes(range(256)) * 50
        up = await put(client, f"{OUT}/r.bin", data)
        assert up.status_code == 200 and up.json()["data"]["size"] == len(data)
        dl = await client.get(
            "/internal/files/download", params={**WHO, "path": f"{OUT}/r.bin"}, headers=AUTH
        )
        assert dl.status_code == 200 and dl.content == data
        assert dl.headers["x-file-size"] == str(len(data))
        assert int(dl.headers["x-file-mtime-ms"]) > 0
        st = await client.post(
            "/internal/files/stat", json={**WHO, "path": f"{OUT}/r.bin"}, headers=AUTH
        )
        assert st.json()["data"]["entry"]["kind"] == "file"


async def test_http_upload_conflict_and_forbidden_path(api):
    _mgr, _backend, client = api
    async with client:
        assert (await put(client, "/workspace/a", b"1")).status_code == 200
        again = await put(client, "/workspace/a", b"2")
        assert (again.status_code, again.json()["data"]["kind"]) == (409, "file_exists")
        assert (await put(client, "/workspace/a", b"2", overwrite="true")).status_code == 200
        bad = await put(client, "/etc/x", b"2")
        assert (bad.status_code, bad.json()["data"]["kind"]) == (403, "path_forbidden")


async def test_http_missing_file_is_404_and_routes_need_the_token(api):
    _mgr, _backend, client = api
    async with client:
        miss = await client.get(
            "/internal/files/download", params={**WHO, "path": "/workspace/none"}, headers=AUTH
        )
        assert (miss.status_code, miss.json()["data"]["kind"]) == (404, "file_not_found")
        for method, url in [
            ("GET", "/internal/files/download"),
            ("PUT", "/internal/files/upload"),
            ("POST", "/internal/files/list"),
            ("POST", "/internal/artifacts/list"),
            ("GET", "/internal/artifacts/whatever"),
        ]:
            resp = await client.request(method, url, params=WHO)
            assert resp.status_code == 401, url


async def test_http_artifact_flow(api):
    _mgr, _backend, client = api
    async with client:
        await put(client, f"{OUT}/deck.pptx", b"PK-deck")
        listed = await client.post(
            "/internal/artifacts/list", json={**WHO, "conversation_id": "c1"}, headers=AUTH
        )
        art = listed.json()["data"]["artifacts"][0]
        assert art["name"] == "deck.pptx"
        got = await client.get(f"/internal/artifacts/{art['aid']}", params=WHO, headers=AUTH)
        assert got.status_code == 200 and got.content == b"PK-deck"
        assert got.headers["content-type"].startswith("application/vnd.openxmlformats")
        forged = await client.get("/internal/artifacts/bad.aid", params=WHO, headers=AUTH)
        assert (forged.status_code, forged.json()["data"]["kind"]) == (410, "artifact_gone")
        other = await client.get(
            f"/internal/artifacts/{art['aid']}", params={**WHO, "end_user_id": "u2"}, headers=AUTH
        )
        assert other.status_code in (401, 410)


async def test_http_list_and_delete(api):
    _mgr, _backend, client = api
    async with client:
        await put(client, "/workspace/d/a.txt", b"a")
        listed = await client.post(
            "/internal/files/list", json={**WHO, "path": "/workspace/d"}, headers=AUTH
        )
        assert [e["name"] for e in listed.json()["data"]["entries"]] == ["a.txt"]
        deleted = await client.post(
            "/internal/files/delete", json={**WHO, "path": "/workspace/d"}, headers=AUTH
        )
        assert deleted.json()["data"] == {"deleted": True}
