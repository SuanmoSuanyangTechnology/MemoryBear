"""Live end-to-end acceptance check for execute, terminal, files and lifecycle APIs.

Uses only the Python standard library. It creates a unique end user, verifies Python state,
terminal exit status, uploaded input and downloadable output, closes the conversation, and
always attempts destroy-user cleanup. The bearer token is never printed.
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


class Api:
    def __init__(self, base: str, token: str, timeout: float) -> None:
        self.base = base.rstrip("/") + "/internal"
        self.token = token
        self.timeout = timeout

    def raw(
        self,
        method: str,
        path: str,
        *,
        body: dict | None = None,
        data: bytes | None = None,
        query: dict | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        url = self.base + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        headers = {"Authorization": f"Bearer {self.token}"}
        payload = data
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        elif data is not None:
            headers["Content-Type"] = "application/octet-stream"
        request = urllib.request.Request(url, data=payload, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.status, dict(response.headers.items()), response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers.items()), exc.read()

    def json(self, method: str, path: str, **kwargs) -> tuple[int, dict]:
        status, _headers, raw = self.raw(method, path, **kwargs)
        try:
            return status, json.loads(raw) if raw else {}
        except ValueError:
            return status, {}


def is_json_success(status: int, payload: dict) -> bool:
    return status == 200 and payload.get("code") == 0


class Checks:
    def __init__(self) -> None:
        self.failures = 0

    def check(self, condition: bool, label: str, detail: str = "") -> None:
        print(f"[{'PASS' if condition else 'FAIL'}] {label}")
        if not condition:
            self.failures += 1
            if detail:
                print(f"       {detail[:300]}")


def load_token(explicit: str | None) -> str:
    if explicit and explicit.strip():
        return explicit.strip()
    token = os.environ.get("SANDBOX_API_TOKEN", "").strip()
    if token:
        return token
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("SANDBOX_API_TOKEN="):
                return line.split("=", 1)[1].strip().strip("\"'")
    sys.exit("token missing: use --token or SANDBOX_API_TOKEN")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=os.getenv("SANDBOX_BASE_URL", "http://127.0.0.1:8010"))
    parser.add_argument("--token", help="Bearer token; prefer SANDBOX_API_TOKEN in automation")
    parser.add_argument("--timeout", type=float, default=340.0)
    args = parser.parse_args()
    api = Api(args.base, load_token(args.token), args.timeout)
    checks = Checks()
    suffix = uuid.uuid4().hex[:12]
    who = {
        "tenant_id": "e2e-test",
        "workspace_id": "acceptance",
        "end_user_id": f"user-{suffix}",
    }
    conversation = f"conversation-{suffix}"
    common = {**who, "conversation_id": conversation}
    out_path = f"/workspace/sessions/{conversation}/out/result.txt"
    in_path = f"/workspace/sessions/{conversation}/in/input.txt"
    uploaded = b"MemoryBear sandbox E2E\n"

    try:
        status, payload = api.json(
            "POST",
            "/execute",
            body={**common, "code": "value = 41\nprint(value)", "timeout_s": 60},
        )
        checks.check(
            is_json_success(status, payload)
            and (payload.get("data") or {}).get("stdout", "").strip() == "41",
            "Python execute succeeds",
            str(payload),
        )
        status, payload = api.json(
            "POST",
            "/execute",
            body={**common, "code": "print(value + 1)", "timeout_s": 60},
        )
        checks.check(
            is_json_success(status, payload)
            and (payload.get("data") or {}).get("stdout", "").strip() == "42",
            "same-conversation Python state is retained",
            str(payload),
        )

        status, payload = api.json(
            "POST",
            "/commands",
            body={**common, "cmd": "printf terminal-ok", "timeout_s": 60},
        )
        command_data = payload.get("data") or {}
        checks.check(
            is_json_success(status, payload)
            and command_data.get("exit_code") == 0
            and command_data.get("stdout") == "terminal-ok",
            "terminal returns real stdout and exit_code",
            str(payload),
        )

        status, payload = api.json(
            "PUT",
            "/files/upload",
            data=uploaded,
            query={**who, "path": in_path, "overwrite": "false"},
        )
        checks.check(
            is_json_success(status, payload)
            and (payload.get("data") or {}).get("size") == len(uploaded),
            "input file uploads",
            str(payload),
        )
        code = (
            "from pathlib import Path\n"
            f"src = Path({in_path!r}).read_bytes()\n"
            f"dst = Path({out_path!r})\n"
            "dst.parent.mkdir(parents=True, exist_ok=True)\n"
            "dst.write_bytes(src + b'generated\\n')\n"
            "print(len(src))"
        )
        status, payload = api.json(
            "POST", "/execute", body={**common, "code": code, "timeout_s": 60}
        )
        checks.check(
            is_json_success(status, payload),
            "sandbox transforms input into an output artifact",
            str(payload),
        )

        status, payload = api.json("POST", "/artifacts/list", body=common)
        artifacts = (payload.get("data") or {}).get("artifacts") or []
        artifact = next((item for item in artifacts if item.get("path") == out_path), None)
        checks.check(
            is_json_success(status, payload) and artifact is not None,
            "output artifact is listed",
            str(payload),
        )
        aid = str((artifact or {}).get("aid") or "invalid")
        status, _headers, downloaded = api.raw(
            "GET",
            "/artifacts/" + urllib.parse.quote(aid, safe=""),
            query=who,
        )
        expected = uploaded + b"generated\n"
        same_digest = hashlib.sha256(downloaded).digest() == hashlib.sha256(expected).digest()
        checks.check(
            status == 200 and same_digest,
            "artifact download is byte-identical",
            f"HTTP {status}",
        )

        status, payload = api.json("POST", "/close-conversation", body=common)
        closed = (payload.get("data") or {}).get("closed_kernels", 0)
        checks.check(
            is_json_success(status, payload) and closed >= 2,
            "close-conversation retires code and shell kernels",
            str(payload),
        )
    except (OSError, TimeoutError, ValueError) as exc:
        checks.check(False, "live service remains reachable", f"{type(exc).__name__}: {exc}")
    finally:
        try:
            status, payload = api.json("POST", "/destroy-user", body=who)
            checks.check(
                is_json_success(status, payload),
                "destroy-user cleanup succeeds",
                str(payload),
            )
        except (OSError, TimeoutError, ValueError) as exc:
            checks.check(False, "destroy-user cleanup succeeds", str(exc))

    print(f"RESULT: failures={checks.failures}")
    return 0 if checks.failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
