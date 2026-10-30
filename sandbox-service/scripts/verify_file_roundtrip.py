"""Live check of the file flow (design doc 7) against a running sandbox-service.

Standard library only:

    python scripts/verify_file_roundtrip.py --base http://127.0.0.1:8010

For one fresh end user (its sandbox is destroyed at the end) it checks:

1. a file written to ``out/`` by a command is listed as an artifact and the streamed
   download is byte-identical (sha256) to what the sandbox holds;
2. a file uploaded to ``in/`` is byte-identical inside the sandbox;
3. stat / list / delete, overwrite=false conflict;
4. refusals: path outside /workspace, tampered aid, another user's aid, a file that
   changed after it was listed.

Exit code 0 when every check passes, 1 otherwise.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path


def load_token() -> str:
    token = os.environ.get("SANDBOX_API_TOKEN", "").strip()
    if token:
        return token
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("SANDBOX_API_TOKEN="):
                return line.split("=", 1)[1].strip().strip("\"'")
    sys.exit("SANDBOX_API_TOKEN not found in the environment or in .env")


def _lower(headers) -> dict:
    return {k.lower(): v for k, v in headers.items()}


class Api:
    def __init__(self, base: str, token: str) -> None:
        self.base = base.rstrip("/") + "/internal"
        self.token = token

    def raw(self, method: str, path: str, *, body=None, data=None, query=None, timeout=340):
        url = self.base + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        headers = {"Authorization": f"Bearer {self.token}"}
        payload = None
        if body is not None:
            payload = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        elif data is not None:
            payload = data
            headers["Content-Type"] = "application/octet-stream"
        req = urllib.request.Request(url, data=payload, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, _lower(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, _lower(exc.headers), exc.read()

    def json(self, method: str, path: str, **kw):
        status, _h, content = self.raw(method, path, **kw)
        try:
            return status, json.loads(content)
        except ValueError:
            return status, {}


class Checker:
    def __init__(self) -> None:
        self.failed = 0

    def check(self, ok: bool, label: str, detail: str = "") -> bool:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  ({detail})" if detail and not ok else ""))
        if not ok:
            self.failed += 1
        return ok


def kind(resp: dict) -> str:
    return str(((resp or {}).get("data") or {}).get("kind"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default=os.environ.get("SANDBOX_BASE_URL", "http://127.0.0.1:8010"))
    ap.add_argument("--size-mb", type=float, default=8, help="size of the binary test file")
    args = ap.parse_args()

    api = Api(args.base, load_token())
    suffix = uuid.uuid4().hex[:12]
    who = {"tenant_id": "t-verify", "workspace_id": "w-verify", "end_user_id": f"u-{suffix}"}
    other = {**who, "end_user_id": f"u-other-{suffix}"}
    conv = f"conv-{suffix[:6]}"
    out = f"/workspace/sessions/{conv}/out"
    inn = f"/workspace/sessions/{conv}/in"
    ck = Checker()

    def cmd(line: str):
        s, r = api.json("POST", "/commands", body={**who, "conversation_id": conv, "cmd": line, "timeout_s": 120})
        return s, (r.get("data") or {})

    try:
        s, _ = api.json("GET", "/stats")
        if s != 200:
            print(f"service not reachable or token rejected: HTTP {s}")
            return 1

        print("1. sandbox -> artifact -> main app (download)")
        size = int(args.size_mb * 1024 * 1024)
        s, d = cmd(
            f"mkdir -p {out} && head -c {size} /dev/urandom > {out}/blob.pdf"
            f" && printf 'a,b\\n1,2\\n' > {out}/table.csv && sha256sum {out}/blob.pdf"
        )
        ck.check(s == 200 and d.get("exit_code") == 0, "command wrote files into out/", str(d)[:200])
        sandbox_sha = (d.get("stdout") or "").split()[0] if d.get("stdout") else ""

        s, r = api.json("POST", "/artifacts/list", body={**who, "conversation_id": conv})
        arts = {a["name"]: a for a in (r.get("data") or {}).get("artifacts", [])}
        ck.check(s == 200 and set(arts) == {"blob.pdf", "table.csv"}, "artifacts listed", str(r)[:300])
        blob = arts.get("blob.pdf", {})
        ck.check(blob.get("size") == size, "artifact size matches", f"{blob.get('size')} vs {size}")
        ck.check(blob.get("content_type") == "application/pdf", "content_type from the extension", str(blob.get("content_type")))

        s, h, content = api.raw("GET", f"/artifacts/{urllib.parse.quote(blob.get('aid', 'x'), safe='')}", query=who)
        got = hashlib.sha256(content).hexdigest()
        ck.check(s == 200 and got == sandbox_sha, "artifact download is byte-identical (sha256)", f"HTTP {s} {got} vs {sandbox_sha}")
        ck.check(int(h.get("content-length", -1)) == size, "Content-Length header is the file size")

        print("2. main app -> sandbox (upload to in/)")
        payload = os.urandom(3 * 1024 * 1024 + 17)
        target = f"{inn}/data.bin"
        s, r = api.json("PUT", "/files/upload", data=payload, query={**who, "path": target, "overwrite": "false"})
        ck.check(s == 200 and (r.get("data") or {}).get("size") == len(payload), "upload stored", f"HTTP {s} {r}"[:200])
        s, d = cmd(f"sha256sum {target}")
        ck.check(
            s == 200 and (d.get("stdout") or "").split()[:1] == [hashlib.sha256(payload).hexdigest()],
            "uploaded file is byte-identical inside the sandbox", str(d)[:200],
        )
        s, r = api.json("PUT", "/files/upload", data=b"x", query={**who, "path": target, "overwrite": "false"})
        ck.check(s == 409 and kind(r) == "file_exists", "overwrite=false on an existing file -> file_exists", f"HTTP {s} {kind(r)}")
        s, r = api.json("PUT", "/files/upload", data=b"y", query={**who, "path": target, "overwrite": "true"})
        ck.check(s == 200, "overwrite=true replaces it", f"HTTP {s}")

        print("3. stat / list / delete")
        s, r = api.json("POST", "/files/stat", body={**who, "path": target})
        ck.check(s == 200 and (r["data"]["entry"]["size"] == 1), "stat reports the new size", str(r)[:200])
        s, r = api.json("POST", "/files/list", body={**who, "path": f"/workspace/sessions/{conv}", "depth": 2})
        names = {e["name"] for e in (r.get("data") or {}).get("entries", [])}
        ck.check(s == 200 and {"blob.pdf", "data.bin"} <= names, "list sees out/ and in/ files", str(names))
        s, r = api.json("POST", "/files/delete", body={**who, "path": target})
        ck.check(s == 200, "delete", f"HTTP {s}")
        s, r = api.json("POST", "/files/stat", body={**who, "path": target})
        ck.check(s == 404 and kind(r) == "file_not_found", "stat after delete -> file_not_found", f"HTTP {s} {kind(r)}")

        print("4. refusals")
        s, r = api.json("POST", "/files/stat", body={**who, "path": "/etc/passwd"})
        ck.check(s == 403 and kind(r) == "path_forbidden", "path outside /workspace -> path_forbidden", f"HTTP {s} {kind(r)}")
        s, r = api.json("POST", "/files/stat", body={**who, "path": "/workspace/../etc/passwd"})
        ck.check(s == 403 and kind(r) == "path_forbidden", "dot-dot escape -> path_forbidden", f"HTTP {s} {kind(r)}")
        aid = arts.get("table.csv", {}).get("aid", "x")
        s, _h, c = api.raw("GET", f"/artifacts/{urllib.parse.quote(aid[:-3] + 'abc', safe='')}", query=who)
        ck.check(s == 410, "tampered aid is rejected (artifact_gone, indistinguishable from expired)", f"HTTP {s}")
        s, _h, c = api.raw("GET", f"/artifacts/{urllib.parse.quote(aid, safe='')}", query=other)
        ck.check(s in (401, 403, 410), "another user cannot use the aid", f"HTTP {s}")
        cmd(f"printf 'changed after listing\\n' >> {out}/table.csv")
        s, _h, c = api.raw("GET", f"/artifacts/{urllib.parse.quote(aid, safe='')}", query=who)
        ck.check(s == 410, "file changed after listing -> artifact_gone (410)", f"HTTP {s}")
    finally:
        s, _ = api.json("POST", "/destroy-user", body=who)
        print(f"cleanup: destroy-user -> HTTP {s}")

    print("RESULT:", "all checks passed" if ck.failed == 0 else f"{ck.failed} check(s) failed")
    return 0 if ck.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
