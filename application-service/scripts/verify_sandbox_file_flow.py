"""Live check of the main-app side of sandbox file flow against a real sandbox-service and MinIO.

Run from ``core/application-service`` (needs the DB from ``.env``, a running sandbox-service and MinIO):

    python scripts/verify_sandbox_file_flow.py [--minio http://127.0.0.1:19000]

Out (publish): files written to ``out/`` by a command -> ``publish_artifacts`` -> MinIO object
(sha256 identical) + ``FileMetadata`` completed; a disallowed type and a magic-number
mismatch are refused without leaving a metadata row in a non-failed state.
In (import): a file stored in MinIO -> ``import_uploaded_file`` -> ``in/`` in the sandbox
(sha256 identical there).

It creates random tenant/workspace ids and an end user, and removes the MinIO objects,
the ``FileMetadata`` rows and the sandbox at the end. Exit code 0 when every check passes.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import sys
import uuid
from pathlib import Path

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("--minio", default=os.environ.get("VERIFY_MINIO_URL", "http://127.0.0.1:19000"))
args = ap.parse_args()

# Must be set before app.core.config is imported (load_dotenv never overrides these).
os.environ["STORAGE_TYPE"] = "minio"
os.environ["MINIO_ENDPOINT_URL"] = args.minio
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, select  # noqa: E402

import src.main  # noqa: E402,F401  (module-level create_app() registers the process runtime the DB helpers need)
from src.config import current_settings  # noqa: E402
from src.db import get_async_db_context  # noqa: E402
from src.integrations.sandbox.client import SandboxPrincipal, SandboxServiceClient  # noqa: E402
from src.models.file_metadata_model import FileMetadata  # noqa: E402
from src.services import sandbox_file_service as svc  # noqa: E402
from src.services.file_storage_service import generate_file_key, get_file_storage_service  # noqa: E402

failed = 0


def check(ok: bool, label: str, detail: str = "") -> bool:
    global failed
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        failed += 1
    return ok


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def main() -> None:
    client = SandboxServiceClient.from_settings(current_settings())
    storage = get_file_storage_service()
    tenant, workspace = uuid.uuid4(), uuid.uuid4()
    principal = SandboxPrincipal(str(tenant), str(workspace), f"u-{uuid.uuid4().hex[:12]}")
    conv = f"conv-{uuid.uuid4().hex[:8]}"
    out, inn = svc.out_dir(conv), svc.in_dir(conv)
    keys: list[str] = []
    file_ids: list[uuid.UUID] = []

    async def cmd(line: str):
        return await client.run_command(principal, conversation_id=conv, cmd=line, timeout_s=60)

    try:
        print(f"storage backend: {type(storage.storage).__name__} @ {args.minio}")
        print("1. sandbox out/ -> main app storage (publish)")
        r = await cmd(
            f"mkdir -p {out} && cd {out} && "
            r"{ printf '%%PDF-1.4\n'; head -c 3000000 /dev/urandom; } > report.pdf && "
            "printf 'a,b\\n1,2\\n' > table.csv && "
            "printf 'MZ' > evil.exe && "
            "head -c 5000 /dev/urandom > fake.pdf && "
            "sha256sum report.pdf table.csv"
        )
        check(r.exit_code == 0, "command wrote files into out/", f"{r.stderr[:200]}")
        sandbox_sha = {ln.split()[1]: ln.split()[0] for ln in r.stdout.splitlines() if ln.strip()}

        arts, _ = await client.list_artifacts(principal, conv)
        by_name = {a.name: a for a in arts}
        check(len(arts) == 4, "4 artifacts listed", str([a.name for a in arts]))

        report = await svc.publish_artifacts(client, principal, arts)
        published = {p.name: p for p in report.published}
        failures = {f.name: f.reason for f in report.failed}
        check(set(published) == {"report.pdf", "table.csv"}, "pdf and csv published", str(published.keys()))
        check("evil.exe" in failures, "executable refused", str(failures))
        check("fake.pdf" in failures, "pdf with a wrong magic number refused", str(failures))

        async with get_async_db_context() as db:
            for name, item in published.items():
                file_ids.append(uuid.UUID(item.file_id))
                meta = (
                    await db.execute(select(FileMetadata).where(FileMetadata.id == uuid.UUID(item.file_id)))
                ).scalar_one_or_none()
                check(
                    meta is not None and meta.status == "completed" and meta.file_size == item.size,
                    f"{name}: FileMetadata completed with the right size",
                )
                if meta is not None:
                    keys.append(meta.file_key)
                    data = await storage.download_file(meta.file_key)
                    check(
                        sha(data) == sandbox_sha.get(name),
                        f"{name}: object in MinIO is byte-identical to the sandbox file",
                        f"{sha(data)} vs {sandbox_sha.get(name)}",
                    )
            rows = (
                await db.execute(
                    select(FileMetadata).where(
                        FileMetadata.tenant_id == tenant, FileMetadata.status != "completed"
                    )
                )
            ).scalars().all()
            for row in rows:
                file_ids.append(row.id)
                keys.append(row.file_key)
            # evil.exe is refused before any row exists; fake.pdf fails mid-stream -> "failed"
            check(
                all(row.status == "failed" for row in rows),
                "no pending row is left behind (failed uploads are marked failed)",
                str([(r.file_name, r.status) for r in rows]),
            )
        check(by_name.get("report.pdf") is not None, "report.pdf was in the artifact list")

        print("2. main app storage -> sandbox in/ (import)")
        payload = os.urandom(6 * 1024 * 1024 + 3)
        up_id = uuid.uuid4()
        key = generate_file_key(tenant, workspace, up_id, ".bin")
        keys.append(key)
        file_ids.append(up_id)
        await storage.storage.upload(key, payload, "application/octet-stream")
        async with get_async_db_context() as db:
            db.add(
                FileMetadata(
                    id=up_id, tenant_id=tenant, workspace_id=workspace, file_key=key,
                    file_name="data set.bin", file_ext=".bin", file_size=len(payload),
                    content_type="application/octet-stream", status="completed",
                )
            )
            await db.commit()
        imported = await svc.import_uploaded_file(
            client, principal, conversation_id=conv, file_id=str(up_id)
        )
        check(imported.size == len(payload), "import reports the stored size", f"{imported.size}")
        check(imported.sandbox_path.startswith(inn + "/"), "file landed under in/", imported.sandbox_path)
        r = await cmd(f"sha256sum '{imported.sandbox_path}'")
        got = (r.stdout.split() or [""])[0]
        check(got == sha(payload), "file inside the sandbox is byte-identical", f"{got} vs {sha(payload)}")

        other = uuid.uuid4()
        try:
            await svc.import_uploaded_file(client, principal, conversation_id=conv, file_id=str(other))
            check(False, "unknown file_id is refused")
        except svc.SandboxFileError:
            check(True, "unknown file_id is refused")
        foreign = SandboxPrincipal(str(uuid.uuid4()), str(uuid.uuid4()), principal.end_user_id)
        try:
            await svc.import_uploaded_file(client, foreign, conversation_id=conv, file_id=str(up_id))
            check(False, "a file of another workspace cannot be imported")
        except svc.SandboxFileError:
            check(True, "a file of another workspace cannot be imported")
    finally:
        for key in keys:
            try:
                await storage.delete_file(key)
            except Exception:  # noqa: BLE001
                pass
        async with get_async_db_context() as db:
            await db.execute(delete(FileMetadata).where(FileMetadata.tenant_id == tenant))
            await db.commit()
        try:
            destroyed = await client.destroy_user(principal)
            print(f"cleanup: objects, metadata rows and sandbox removed (destroyed={destroyed})")
        finally:
            await client.aclose()


asyncio.run(main())
print("RESULT:", "all checks passed" if failed == 0 else f"{failed} check(s) failed")
sys.exit(0 if failed == 0 else 1)
