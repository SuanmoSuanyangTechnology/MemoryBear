"""Live check of the per-user kernel quota and idle kernel reaping.

Needs a running sandbox-service on a real backend started with a short
``SANDBOX_KERNEL_IDLE_S`` (the script's ``--idle`` must match it). Only the standard
library is used, so any Python 3 works:

    python scripts/verify_kernel_reaping.py --idle 60

What it does, for one fresh end user (all of its sandbox is destroyed at the end):

1. conversations A, B, C run code (A sets ``x = 1``), conversation D starts a background
   ``sleep 601`` through the terminal. That is 4 kernels, the per-user limit.
2. conversation E is refused with ``quota_exceeded`` (HTTP 402, code 3018).
3. it waits, without touching A-D, until ``kernels_reaped`` in ``/internal/stats`` grows.
4. E now succeeds, A reports ``kernel_reset`` (``x`` is gone), and no ``sleep 601`` is left.

Exit code 0 when every check passes, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

QUOTA_CODE = 3018
SLEEP_MARK = "sleep 601"
# The sandbox image has no pgrep: count matching processes by scanning /proc.
COUNT_SLEEP = (
    "for p in /proc/[0-9]*; do tr '\\0' ' ' < $p/cmdline 2>/dev/null; echo; done"
    f" | grep -c '^{SLEEP_MARK}'"
)


class Api:
    def __init__(self, base: str, token: str) -> None:
        self.base = base.rstrip("/")
        self.token = token

    def call(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=340) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read())
            except ValueError:
                payload = {}
            return exc.code, payload


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


class Checker:
    def __init__(self) -> None:
        self.failed = 0

    def check(self, ok: bool, label: str, detail: str = "") -> bool:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  ({detail})" if detail and not ok else ""))
        if not ok:
            self.failed += 1
        return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default=os.environ.get("SANDBOX_BASE_URL", "http://127.0.0.1:8010"))
    ap.add_argument("--idle", type=float, default=60, help="SANDBOX_KERNEL_IDLE_S of the running service")
    ap.add_argument("--tick", type=float, default=30, help="SANDBOX_TICK_INTERVAL_S of the running service")
    ap.add_argument("--max-kernels", type=int, default=4, help="SANDBOX_MAX_KERNELS_PER_USER")
    args = ap.parse_args()

    api = Api(args.base + "/internal", load_token())
    who = {"tenant_id": "t-verify", "workspace_id": "w-verify", "end_user_id": f"u-{uuid.uuid4().hex[:12]}"}
    ck = Checker()

    def code(conv: str, src: str) -> tuple[int, dict]:
        return api.call("POST", "/execute", {**who, "conversation_id": conv, "code": src, "timeout_s": 60})

    def cmd(conv: str, line: str) -> tuple[int, dict]:
        return api.call("POST", "/commands", {**who, "conversation_id": conv, "cmd": line, "timeout_s": 60})

    def reaped() -> int:
        _, body = api.call("GET", "/stats")
        return int(((body.get("data") or {}).get("metrics") or {}).get("kernels_reaped", 0))

    try:
        status, _ = api.call("GET", "/stats")
        if status != 200:
            print(f"service not reachable or token rejected: HTTP {status}")
            return 1
        base_reaped = reaped()
        print(f"end user {who['end_user_id']}, kernels_reaped before = {base_reaped}")

        print("1. fill the per-user kernel quota")
        convs = [f"conv-{c}-{uuid.uuid4().hex[:6]}" for c in "ABCDE"]
        a, b, c, d, e = convs
        s, r = code(a, "x = 1\nprint('a-ok')")  # first call creates the microVM, may take a while
        ck.check(s == 200 and r["data"]["status"] == "ok", "A: python runs, x = 1", str(r)[:200])
        ck.check(code(b, "print('b-ok')")[0] == 200, "B: python runs")
        ck.check(code(c, "print('c-ok')")[0] == 200, "C: python runs")
        s, r = cmd(d, f"{SLEEP_MARK} & sleep 0.5; {COUNT_SLEEP}")
        data = r.get("data") or {}
        ck.check(
            s == 200 and data.get("exit_code") == 0 and data.get("stdout", "").strip() == "1",
            "D: terminal starts a background process and it is alive",
            f"exit_code={data.get('exit_code')} stdout={data.get('stdout')!r} stderr={data.get('stderr')!r}",
        )
        t_last_use = time.monotonic()

        print(f"2. the {args.max_kernels + 1}th conversation is refused")
        s, r = code(e, "print('e-ok')")
        ck.check(s == 402 and r.get("code") == QUOTA_CODE, "E: quota_exceeded (HTTP 402, code 3018)", f"HTTP {s} {r}"[:200])

        print(f"3. wait for idle reaping (idle {args.idle:g}s, tick {args.tick:g}s) without touching A-D")
        deadline = t_last_use + args.idle + 2 * args.tick + 30
        while time.monotonic() < deadline and reaped() - base_reaped < 4:
            time.sleep(5)
        got = reaped() - base_reaped
        waited = time.monotonic() - t_last_use
        ck.check(got >= 4, f"kernels_reaped grew by {got} (need 4) after {waited:.0f}s", "is SANDBOX_KERNEL_IDLE_S set and the service restarted?")

        print("4. state after reaping")
        s, r = code(e, "print('e-ok')")
        ck.check(s == 200 and r["data"]["status"] == "ok", "E: now succeeds, a slot was freed", f"HTTP {s} {r}"[:200])
        s, r = code(a, "print(x)")
        data = r.get("data") or {}
        ck.check(s == 200 and data.get("kernel_reset") is True, "A: kernel was rebuilt (kernel_reset), x is gone", str(data)[:200])
        s, r = cmd(e, COUNT_SLEEP)
        data = r.get("data") or {}
        ck.check(data.get("stdout", "").strip() == "0", "background process of D was killed", f"stdout={data.get('stdout')!r}")
    finally:
        s, _ = api.call("POST", "/destroy-user", who)
        print(f"cleanup: destroy-user -> HTTP {s}")

    print("RESULT:", "all checks passed" if ck.failed == 0 else f"{ck.failed} check(s) failed")
    return 0 if ck.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
