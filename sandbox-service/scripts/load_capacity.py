"""Concurrent capacity/queue load check against a running sandbox-service.

Creates one sandbox identity per request, runs a trivial execute call concurrently, reports
throughput, latency percentiles and outcome counts, then destroys every created sandbox.
The bearer token is accepted via --token, SANDBOX_API_TOKEN, or the service .env file and is
never printed.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Result:
    outcome: str
    latency_ms: float
    user_id: str
    detail: str = ""


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


def post(base: str, token: str, path: str, body: dict, timeout: float) -> tuple[int, dict]:
    request = urllib.request.Request(
        base.rstrip("/") + "/internal/" + path.lstrip("/"),
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            payload = json.loads(raw) if raw else {}
        except ValueError:
            payload = {}
        return exc.code, payload


def percentile(values: list[float], percent: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percent
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def run_one(
    base: str,
    token: str,
    tenant: str,
    workspace: str,
    run_id: str,
    index: int,
    timeout: float,
) -> Result:
    user_id = f"load-{run_id}-{index}"
    body = {
        "tenant_id": tenant,
        "workspace_id": workspace,
        "end_user_id": user_id,
        "conversation_id": "capacity",
        "code": "print(1)",
        "timeout_s": min(timeout, 300.0),
    }
    started = time.perf_counter()
    try:
        status, payload = post(base, token, "execute", body, timeout)
        elapsed = (time.perf_counter() - started) * 1000
        data = payload.get("data") if isinstance(payload, dict) else {}
        data = data if isinstance(data, dict) else {}
        if status == 200 and payload.get("code") == 0:
            return Result("success", elapsed, user_id)
        kind = str(data.get("kind") or payload.get("msg") or f"http_{status}")
        if kind == "sandbox_busy":
            return Result("sandbox_busy", elapsed, user_id, kind)
        return Result("business_error", elapsed, user_id, kind)
    except (OSError, TimeoutError, ValueError) as exc:
        elapsed = (time.perf_counter() - started) * 1000
        return Result("transport_error", elapsed, user_id, type(exc).__name__)


def cleanup(base: str, token: str, tenant: str, workspace: str, result: Result) -> bool:
    """Idempotently clean every generated identity, including timed-out/failed requests."""
    body = {
        "tenant_id": tenant,
        "workspace_id": workspace,
        "end_user_id": result.user_id,
    }
    try:
        status, payload = post(base, token, "destroy-user", body, 60)
        if status == 200 and payload.get("code") == 0:
            return True
    except (OSError, TimeoutError, ValueError):
        pass
    print(f"warning: cleanup failed for {result.user_id}", file=sys.stderr)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=os.getenv("SANDBOX_BASE_URL", "http://127.0.0.1:8010"))
    parser.add_argument("--token", help="Bearer token; prefer SANDBOX_API_TOKEN in automation")
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument(
        "--min-success-rate",
        type=float,
        default=0.01,
        help="minimum successful request ratio (0..1); business/transport errors always fail",
    )
    parser.add_argument("--tenant", default="load-test")
    parser.add_argument("--workspace", default="capacity")
    args = parser.parse_args()
    if args.requests < 1 or args.concurrency < 1 or args.timeout <= 0:
        parser.error("requests, concurrency and timeout must be positive")
    if not 0 <= args.min_success_rate <= 1:
        parser.error("min-success-rate must be between 0 and 1")

    token = load_token(args.token)
    run_id = uuid.uuid4().hex[:10]
    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [
            pool.submit(
                run_one,
                args.base,
                token,
                args.tenant,
                args.workspace,
                run_id,
                index,
                args.timeout,
            )
            for index in range(args.requests)
        ]
        results = [future.result() for future in concurrent.futures.as_completed(futures)]
    wall_s = time.perf_counter() - started

    counts = Counter(result.outcome for result in results)
    latencies = [result.latency_ms for result in results]
    business_kinds = Counter(
        result.detail for result in results if result.outcome == "business_error"
    )
    print(f"requests={args.requests} concurrency={args.concurrency} duration_s={wall_s:.3f}")
    print(f"throughput_rps={args.requests / wall_s:.3f}")
    print(
        "outcomes "
        f"success={counts['success']} sandbox_busy={counts['sandbox_busy']} "
        f"business_error={counts['business_error']} transport_error={counts['transport_error']}"
    )
    print(
        "latency_ms "
        f"mean={statistics.fmean(latencies):.1f} p50={percentile(latencies, 0.50):.1f} "
        f"p95={percentile(latencies, 0.95):.1f} p99={percentile(latencies, 0.99):.1f}"
    )
    if business_kinds:
        rendered_kinds = json.dumps(business_kinds, ensure_ascii=False, sort_keys=True)
        print("business_error_kinds=" + rendered_kinds)

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        cleanup_results = list(
            pool.map(
                lambda result: cleanup(
                    args.base, token, args.tenant, args.workspace, result
                ),
                results,
            )
        )
    cleanup_failures = cleanup_results.count(False)
    success_rate = counts["success"] / args.requests
    failures: list[str] = []
    if counts["business_error"]:
        failures.append(f"business_error={counts['business_error']}")
    if counts["transport_error"]:
        failures.append(f"transport_error={counts['transport_error']}")
    if success_rate < args.min_success_rate:
        failures.append(
            f"success_rate={success_rate:.3f} below min_success_rate={args.min_success_rate:.3f}"
        )
    if cleanup_failures:
        failures.append(f"cleanup_failures={cleanup_failures}")
    if failures:
        print("RESULT: FAIL " + "; ".join(failures))
        return 1
    print(
        f"RESULT: PASS success_rate={success_rate:.3f} "
        f"sandbox_busy={counts['sandbox_busy']} cleanup_failures=0"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
