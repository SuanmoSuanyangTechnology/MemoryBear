#!/usr/bin/env python3
"""Stress test for the sandbox server.

Reads configuration from tests/.env (or environment variables).
Sends concurrent code-execution requests and reports latency / throughput.

Usage:
    python tests/stress_test.py                          # default: 50 requests, 10 concurrent
    python tests/stress_test.py -n 200 -c 20             # 200 requests, 20 concurrent
    python tests/stress_test.py --language python3       # Python only
    python tests/stress_test.py --language javascript    # Node.js only
"""

import argparse
import base64
import http.client
import json
import os
import statistics
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

# ── Config ──


def load_env() -> dict[str, str]:
    """Load .env from the script's directory, then overlay os.environ."""
    env: dict[str, str] = {}
    env_file = Path(__file__).resolve().parent / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    # Environment variables take precedence
    env.update({k: v for k, v in os.environ.items() if v})
    return env


ENV = load_env()
BASE_URL = ENV.get("SANDBOX_URL", "http://127.0.0.1:8194")
API_KEY = ENV.get("API_KEY", "sandbox")

# ── Workloads ──

# Realistic Python snippet: imports, loops, JSON operations
PYTHON_CODE = base64.b64encode(
    b"import json,math,itertools\n"
    b"s=sum(x*x for x in range(1,101))\n"
    b"d={'sum':s,'sqrt':round(math.sqrt(s),2)}\n"
    b"print(json.dumps(d))\n"
).decode()

# Realistic Node.js snippet
NODEJS_CODE = base64.b64encode(
    b"let s=0;for(let i=1;i<=100;i++)s+=i*i;\n"
    b"console.log(JSON.stringify({sum:s,sqrt:Math.round(Math.sqrt(s)*100)/100}));\n"
).decode()

WORKLOADS = {
    "python3": PYTHON_CODE,
    "javascript": NODEJS_CODE,
}

# Expected stdout for each workload — validates that sandbox execution
# actually produced the correct result (not just HTTP 200).
EXPECTED_STDOUT = {
    "python3": '{"sum": 338350, "sqrt": 581.68}',
    "javascript": '{"sum":338350,"sqrt":581.68}',
}


# ── Request sender ──


def send_request(language: str) -> tuple[float, int, str]:
    """Send one code-execution request. Returns (latency_ms, http_status, body)."""
    payload = json.dumps({
        "language": language,
        "code": WORKLOADS[language],
    })
    data = payload.encode()

    url = f"{BASE_URL}/v1/sandbox/run"
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "X-Api-Key": API_KEY,
        },
        method="POST",
    )

    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode()
            latency = (time.perf_counter() - t0) * 1000
            return latency, resp.status, body
    except urllib.error.HTTPError as e:
        latency = (time.perf_counter() - t0) * 1000
        body = e.read().decode()
        return latency, e.code, body
    except Exception as e:
        latency = (time.perf_counter() - t0) * 1000
        return latency, 0, str(e)


def check_response(language: str, status: int, body: str) -> Optional[str]:
    """Return None if response is healthy AND stdout matches expected output."""
    if status != 200:
        return f"HTTP {status}"
    try:
        resp = json.loads(body)
    except json.JSONDecodeError:
        return f"bad json: {body[:120]}"
    if resp.get("code") != 0:
        return f"api error code={resp.get('code')} msg={resp.get('message', '')[:80]}"
    # Verify execution output
    data = resp.get("data") or {}
    stdout = (data.get("stdout") or "").strip()
    expected = EXPECTED_STDOUT.get(language, "")
    if stdout != expected:
        return f"wrong output: expected={expected[:60]} got={stdout[:60]}"
    return None


# ── Runner ──


def run_stress(
    language: str,
    total: int,
    concurrency: int,
) -> dict:
    """Execute `total` requests with `concurrency` concurrent workers."""
    latencies: list[float] = []
    errors: list[str] = []

    t_start = time.perf_counter()

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(send_request, language) for _ in range(total)]
        for future in as_completed(futures):
            lat, status, body = future.result()
            err = check_response(language, status, body)
            if err:
                errors.append(err)
            else:
                latencies.append(lat)

    elapsed = time.perf_counter() - t_start
    ok = len(latencies)
    failed = len(errors)

    latencies.sort()
    return {
        "language": language,
        "total": total,
        "ok": ok,
        "failed": failed,
        "elapsed_s": round(elapsed, 2),
        "throughput": round(ok / elapsed, 1) if elapsed > 0 else 0,
        "latency_p50": _percentile(latencies, 50),
        "latency_p75": _percentile(latencies, 75),
        "latency_p95": _percentile(latencies, 95),
        "latency_p99": _percentile(latencies, 99),
        "latency_mean": round(statistics.mean(latencies), 1) if latencies else 0,
        "latency_min": round(min(latencies), 1) if latencies else 0,
        "latency_max": round(max(latencies), 1) if latencies else 0,
        "errors": errors[:5],  # show at most 5
    }


def _percentile(sorted_data: list[float], p: int) -> float:
    if not sorted_data:
        return 0.0
    idx = int(len(sorted_data) * p / 100.0)
    idx = min(idx, len(sorted_data) - 1)
    return round(sorted_data[idx], 1)


# ── Display ──

HEADER = "\033[1;36m"
GREEN = "\033[0;32m"
YELLOW = "\033[0;33m"
RED = "\033[0;31m"
RESET = "\033[0m"


def print_report(results: list[dict]) -> None:
    print()
    print(f"{HEADER}{'='*60}{RESET}")
    print(f"{HEADER}  Sandbox Stress Test Results{RESET}")
    print(f"{HEADER}  Target: {BASE_URL}{RESET}")
    print(f"{HEADER}{'='*60}{RESET}")
    print()

    for r in results:
        color = GREEN if r["failed"] == 0 else YELLOW if r["failed"] < r["total"] * 0.05 else RED
        print(f"  {HEADER}── {r['language']} ──{RESET}")
        print(f"    Requests:  {r['ok']} ok / {r['failed']} failed  ({r['total']} total)")
        print(f"    Duration:  {r['elapsed_s']}s")
        print(f"    Throughput:{color} {r['throughput']} req/s{RESET}")
        print(f"    Latency (ms):")
        print(f"      mean  {r['latency_mean']:>8}")
        print(f"      min   {r['latency_min']:>8}")
        print(f"      p50   {r['latency_p50']:>8}")
        print(f"      p75   {r['latency_p75']:>8}")
        print(f"      p95   {r['latency_p95']:>8}")
        print(f"      p99   {r['latency_p99']:>8}")
        print(f"      max   {r['latency_max']:>8}")
        if r["errors"]:
            print(f"    Sample errors:")
            for e in r["errors"]:
                print(f"      - {e}")
        print()


# ── Main ──


def main() -> None:
    parser = argparse.ArgumentParser(description="Sandbox stress test")
    parser.add_argument("-n", type=int, default=50, help="Total requests (default: 50)")
    parser.add_argument("-c", type=int, default=10, help="Concurrent workers (default: 10)")
    parser.add_argument(
        "--language",
        choices=["python3", "javascript", "all"],
        default="all",
        help="Language to test (default: all)",
    )
    args = parser.parse_args()

    # Quick health check before starting
    print(f"Checking {BASE_URL}/health ... ", end="", flush=True)
    try:
        req = urllib.request.Request(f"{BASE_URL}/health")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            if data.get("ok"):
                print(f"{GREEN}OK{RESET} (workers={data.get('workers')})")
            else:
                print(f"{RED}FAIL{RESET}: server returned ok=false")
                sys.exit(1)
    except Exception as e:
        print(f"{RED}FAIL{RESET}: {e}")
        sys.exit(1)

    languages = (
        ["python3", "javascript"] if args.language == "all" else [args.language]
    )

    results = []
    for lang in languages:
        print(f"\nTesting {lang}: {args.n} requests, {args.c} concurrent ...")
        r = run_stress(lang, args.n, args.c)
        results.append(r)

    print_report(results)


if __name__ == "__main__":
    main()
