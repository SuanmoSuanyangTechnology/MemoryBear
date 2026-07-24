#!/usr/bin/env python3
"""Stress test for the sandbox server with multiple task types and concurrency levels.

Each task type is tested separately at each concurrency level (1/20/50/100/200).
Task types include JSON parsing, computation, string processing, data structures,
and mixed workloads — covering diverse sandbox usage patterns.

After all tests complete, a JSON report is saved to tests/reports/ and a summary
is printed to stdout.  Use --markdown to also generate a Markdown report.

Usage:
    python tests/stress_test.py                              # all tasks, all concurrency levels
    python tests/stress_test.py --task json_parse            # single task type
    python tests/stress_test.py --concurrency 50,100         # specific concurrency levels
    python tests/stress_test.py --language python3           # Python only
    python tests/stress_test.py --multiplier 10              # total = concurrency * 10
    python tests/stress_test.py --markdown                   # also write .md report
    python tests/stress_test.py --warmup                     # warm-up before testing
"""

import argparse
import asyncio
import base64
import json
import os
import random
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    import httpx
except ImportError:
    sys.exit("httpx is required: pip install httpx")


# ── Config ──────────────────────────────────────────────────────────────


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
    env.update({k: v for k, v in os.environ.items() if v})
    return env


ENV = load_env()
BASE_URL = ENV.get("SANDBOX_URL", "http://127.0.0.1:8194")
API_KEY = ENV.get("API_KEY", "sandbox")

REPORTS_DIR = Path(__file__).resolve().parent / "reports"


# ── Task Definitions ─────────────────────────────────────────────────────


@dataclass
class TaskDef:
    name: str
    description: str
    python_code_b64: str  # base64-encoded Python source
    js_code_b64: str  # base64-encoded Node.js source
    python_expected: str  # expected stdout (exact match)
    js_expected: str  # expected stdout (exact match)


def _b64(source: str) -> str:
    """Base64-encode a source string (strips leading/trailing whitespace)."""
    return base64.b64encode(source.strip().encode()).decode()


# ── Task: JSON parsing ───────────────────────────────────────────────────
# Parse nested JSON, extract and aggregate values, re-serialize.

TASK_JSON_PARSE = TaskDef(
    name="json_parse",
    description="Parse nested JSON, extract fields, compute aggregate",
    python_code_b64=_b64("""\
import json
data = json.loads('{"items":[{"value":1},{"value":2},{"value":3}],"metadata":{"version":"1.0","active":true}}')
total = sum(item["value"] for item in data["items"])
result = {"total": total, "count": len(data["items"]), "version": data["metadata"]["version"], "active": data["metadata"]["active"]}
print(json.dumps(result))
"""),
    js_code_b64=_b64("""\
const data = JSON.parse('{"items":[{"value":1},{"value":2},{"value":3}],"metadata":{"version":"1.0","active":true}}');
const total = data.items.reduce((s, i) => s + i.value, 0);
const result = {total: total, count: data.items.length, version: data.metadata.version, active: data.metadata.active};
console.log(JSON.stringify(result));
"""),
    python_expected='{"total": 6, "count": 3, "version": "1.0", "active": true}',
    js_expected='{"total":6,"count":3,"version":"1.0","active":true}',
)

# ── Task: Computation ────────────────────────────────────────────────────
# Sieve of Eratosthenes up to 10000 + aggregate statistics.

TASK_COMPUTATION = TaskDef(
    name="computation",
    description="Prime sieve up to 10000, compute count/sum/max",
    python_code_b64=_b64("""\
import json
def sieve(n):
    is_prime = [True] * (n + 1)
    is_prime[0] = is_prime[1] = False
    for i in range(2, int(n ** 0.5) + 1):
        if is_prime[i]:
            for j in range(i * i, n + 1, i):
                is_prime[j] = False
    return [i for i in range(2, n + 1) if is_prime[i]]
primes = sieve(10000)
result = {"count": len(primes), "sum": sum(primes), "max": max(primes)}
print(json.dumps(result))
"""),
    js_code_b64=_b64("""\
function sieve(n) {
    const isPrime = new Array(n + 1).fill(true);
    isPrime[0] = isPrime[1] = false;
    for (let i = 2; i * i <= n; i++) {
        if (isPrime[i]) {
            for (let j = i * i; j <= n; j += i) isPrime[j] = false;
        }
    }
    const primes = [];
    for (let i = 2; i <= n; i++) if (isPrime[i]) primes.push(i);
    return primes;
}
const primes = sieve(10000);
const result = {count: primes.length, sum: primes.reduce((a,b)=>a+b,0), max: primes[primes.length-1]};
console.log(JSON.stringify(result));
"""),
    python_expected='{"count": 1229, "sum": 5736396, "max": 9973}',
    js_expected='{"count":1229,"sum":5736396,"max":9973}',
)

# ── Task: String processing ──────────────────────────────────────────────
# Regex matching, case transformation, validation.

TASK_STRING_PROCESSING = TaskDef(
    name="string_processing",
    description="Regex word extraction, email/phone detection, case transform",
    python_code_b64=_b64("""\
import json, re
text = "Hello World! hello@test.com 42"
words = re.findall(r'\\b[a-z]+\\b', text, re.IGNORECASE)
has_email = bool(re.search(r'[\\w.+-]+@[\\w-]+\\.[\\w.-]+', text))
result = {"word_count": len(words), "has_email": has_email, "upper": text.upper()}
print(json.dumps(result))
"""),
    js_code_b64=_b64("""\
const text = "Hello World! hello@test.com 42";
const words = text.match(/\\b[a-z]+\\b/gi) || [];
const hasEmail = /[\\w.+-]+@[\\w-]+\\.[\\w.-]+/.test(text);
const result = {word_count: words.length, has_email: hasEmail, upper: text.toUpperCase()};
console.log(JSON.stringify(result));
"""),
    python_expected='{"word_count": 5, "has_email": true, "upper": "HELLO WORLD! HELLO@TEST.COM 42"}',
    js_expected='{"word_count":5,"has_email":true,"upper":"HELLO WORLD! HELLO@TEST.COM 42"}',
)

# ── Task: Data structures ────────────────────────────────────────────────
# List/array generation, filtering, aggregation, sorting.

TASK_DATA_STRUCTURES = TaskDef(
    name="data_structures",
    description="List generation, filter, map-reduce, sum aggregations",
    python_code_b64=_b64("""\
import json
data = list(range(1, 501))
evens = [x for x in data if x % 2 == 0]
odds_sum = sum(x for x in data if x % 2 == 1)
squares = [x * x for x in range(1, 21)]
result = {"even_count": len(evens), "odd_sum": odds_sum, "square_sum": sum(squares), "total": len(data)}
print(json.dumps(result))
"""),
    js_code_b64=_b64("""\
const data = Array.from({length: 500}, (_, i) => i + 1);
const evens = data.filter(x => x % 2 === 0);
const oddsSum = data.filter(x => x % 2 === 1).reduce((a, b) => a + b, 0);
const squares = Array.from({length: 20}, (_, i) => (i + 1) * (i + 1));
const result = {even_count: evens.length, odd_sum: oddsSum, square_sum: squares.reduce((a,b)=>a+b,0), total: data.length};
console.log(JSON.stringify(result));
"""),
    python_expected='{"even_count": 250, "odd_sum": 62500, "square_sum": 2870, "total": 500}',
    js_expected='{"even_count":250,"odd_sum":62500,"square_sum":2870,"total":500}',
)

# ── Task: Mixed ──────────────────────────────────────────────────────────
# JSON config parsing → branching computation → JSON output.

TASK_MIXED = TaskDef(
    name="mixed",
    description="JSON config parse, conditional branching, mixed computation",
    python_code_b64=_b64("""\
import json
config = json.loads('{"tasks":[{"type":"sum","n":100},{"type":"multiply","a":7,"b":6}]}')
results = {}
for task in config["tasks"]:
    if task["type"] == "sum":
        results["triangular"] = task["n"] * (task["n"] + 1) // 2
    elif task["type"] == "multiply":
        results["product"] = task["a"] * task["b"]
results["task_count"] = len(config["tasks"])
results["config_keys"] = sorted(config.keys())
print(json.dumps(results))
"""),
    js_code_b64=_b64("""\
const config = JSON.parse('{"tasks":[{"type":"sum","n":100},{"type":"multiply","a":7,"b":6}]}');
const results = {};
for (const task of config.tasks) {
    if (task.type === 'sum') {
        results.triangular = task.n * (task.n + 1) / 2;
    } else if (task.type === 'multiply') {
        results.product = task.a * task.b;
    }
}
results.task_count = config.tasks.length;
results.config_keys = Object.keys(config).sort();
console.log(JSON.stringify(results));
"""),
    python_expected='{"triangular": 5050, "product": 42, "task_count": 2, "config_keys": ["tasks"]}',
    js_expected='{"triangular":5050,"product":42,"task_count":2,"config_keys":["tasks"]}',
)

# ── Registry ─────────────────────────────────────────────────────────────

ALL_TASKS: dict[str, TaskDef] = {
    t.name: t for t in [
        TASK_JSON_PARSE,
        TASK_COMPUTATION,
        TASK_STRING_PROCESSING,
        TASK_DATA_STRUCTURES,
        TASK_MIXED,
    ]
}

LANGUAGE_MAP = {
    "python3": "python3",
    "javascript": "javascript",
}


# ── HTTP client ──────────────────────────────────────────────────────────


# Shared httpx async client (set up in main() once CLI flags are known).
# Each test run creates its own semaphore for concurrency control, while the
# client itself is shared across runs (connection-pool warmth is preserved).
_http_client: "Optional[httpx.AsyncClient]" = None


def init_http_client(*, keep_alive: bool = True, max_connections: int = 25) -> None:
    """Initialise the module-level async httpx client.

    Parameters
    ----------
    keep_alive:
        When ``True`` (default) connections are reused across requests
        (HTTP keep-alive / connection pooling).  This cuts per-request
        latency by ~80 % vs a new TCP handshake every time.

        Set to ``False`` (``--no-keep-alive``) to force a fresh TCP
        connection per request — useful for verifying LB per-request
        distribution, at the cost of a TCP 3-way handshake each time.
    max_connections:
        Hard ceiling on concurrent TCP connections in the pool.
        Default 25 — well below the Windows ephemeral-port range.
    """
    global _http_client
    limits = httpx.Limits(
        max_connections=max_connections,
        max_keepalive_connections=max_connections if keep_alive else 0,
    )
    # Transport-level retries are OFF — they have zero backoff, which turns
    # a transient network blip into a retry storm.  We handle retry ourselves
    # in send_request() with exponential backoff + jitter.
    _http_client = httpx.AsyncClient(
        base_url=BASE_URL,
        timeout=httpx.Timeout(30.0, connect=30.0),
        limits=limits,
        headers={
            "Content-Type": "application/json",
            "X-Api-Key": API_KEY,
        },
    )


async def close_http_client() -> None:
    """Shut down the shared async client and release connections."""
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None


# ── Request sender ───────────────────────────────────────────────────────

# Exceptions worth retrying (transient network / connection errors).
# httpx wraps low-level socket errors in these types.
_RETRYABLE_EXC = (
    httpx.ReadError,
    httpx.ConnectError,
    httpx.RemoteProtocolError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
)

# Max retries + base backoff for transient failures.
_MAX_RETRIES = 3
_RETRY_BASE_SEC = 0.125   # first retry 125 ms, then 250 ms, then 500 ms


async def send_request(task: TaskDef, language: str) -> tuple[float, int, str]:
    """Send one code-execution request. Returns (latency_ms, http_status, body).

    Transient connection errors are retried up to 3 times with exponential
    backoff + random jitter, so a brief network hiccup doesn't turn into a
    cascade of simultaneous retries across all concurrent tasks.
    """
    assert _http_client is not None, "init_http_client() must be called first"

    code_b64 = task.python_code_b64 if language == "python3" else task.js_code_b64
    payload = {"language": language, "code": code_b64}

    t0 = time.perf_counter()
    last_exc: Optional[Exception] = None

    for attempt in range(1 + _MAX_RETRIES):  # 1 initial + 3 retries
        try:
            resp = await _http_client.post("/v1/sandbox/run", json=payload)
            latency = (time.perf_counter() - t0) * 1000
            return latency, resp.status_code, resp.text
        except _RETRYABLE_EXC as e:
            last_exc = e
            if attempt < _MAX_RETRIES:
                # exponential backoff: 125, 250, 500 ms + 0-25% jitter
                sleep_s = _RETRY_BASE_SEC * (2 ** attempt)
                sleep_s += random.uniform(0, sleep_s * 0.25)
                await asyncio.sleep(sleep_s)
        except Exception as e:
            latency = (time.perf_counter() - t0) * 1000
            return latency, 0, f"{type(e).__name__}: {e}"

    latency = (time.perf_counter() - t0) * 1000
    return latency, 0, f"{type(last_exc).__name__}: {last_exc}"


def check_response(task: TaskDef, language: str, status: int, body: str) -> tuple[Optional[str], Optional[str]]:
    """Check a sandbox response.

    Returns (error_kind, failure_detail).
    - error_kind: short category label for counting / grouping, or None on success.
    - failure_detail: detailed info including full response body, or None on success.
    """
    body_clean = body.strip()
    body_preview = body_clean[:800]

    if status != 200:
        return f"HTTP {status}", f"HTTP {status}\n{body_preview}"
    try:
        resp = json.loads(body_clean)
    except json.JSONDecodeError:
        return "bad json", f"Invalid JSON (HTTP 200):\n{body_preview}"

    api_code = resp.get("code", -1)
    if api_code != 0:
        msg = resp.get("message", "")
        data = resp.get("data", {})
        stderr = (data.get("stderr") or "").strip()
        lines = [f"API error code={api_code} message=\"{msg}\"", json.dumps(resp, indent=2, ensure_ascii=False)[:1200]]
        if stderr:
            lines.append(f"--- stderr ---\n{stderr[:600]}")
        return f"api code={api_code}", "\n".join(lines)

    data = resp.get("data") or {}
    stdout = (data.get("stdout") or "").strip()
    expected = task.python_expected if language == "python3" else task.js_expected
    if stdout != expected:
        lines = [
            f"Expected: {expected}",
            f"Got:      {stdout}",
            json.dumps(resp, indent=2, ensure_ascii=False)[:1200],
        ]
        return "wrong output", "\n".join(lines)
    return None, None


async def warmup(task: TaskDef, language: str, n: int = 3) -> None:
    """Send a few warm-up requests to prime caches / connections."""
    for _ in range(n):
        await send_request(task, language)


# ── Single-run executor ──────────────────────────────────────────────────


@dataclass
class RunResult:
    task_name: str
    language: str
    concurrency: int
    total: int
    ok: int
    failed: int
    elapsed_s: float
    throughput: float  # req/s
    latency_p50: float
    latency_p75: float
    latency_p95: float
    latency_p99: float
    latency_mean: float
    latency_min: float
    latency_max: float
    error_kinds: list[str] = field(default_factory=list)  # short labels for counting
    first_failure: Optional[str] = None  # full response body of first failure


def _percentile(sorted_data: list[float], p: int) -> float:
    if not sorted_data:
        return 0.0
    idx = int(len(sorted_data) * p / 100.0)
    idx = min(idx, len(sorted_data) - 1)
    return round(sorted_data[idx], 1)


async def run_single(task: TaskDef, language: str, total: int, concurrency: int) -> RunResult:
    """Execute `total` requests with up to `concurrency` in-flight at a time.

    Uses ``asyncio`` + ``httpx.AsyncClient`` so concurrency is limited by the
    semaphore rather than by OS threads.  This matches the async nature of the
    sandbox server under test and avoids thread-per-request overhead at high
    concurrency (e.g. 200).
    """
    latencies: list[float] = []
    error_kinds: list[str] = []
    first_failure: Optional[str] = None
    # ``first_failure`` is protected by a lock so the first failure in
    # completion order is captured deterministically.
    first_failure_lock = asyncio.Lock()

    semaphore = asyncio.Semaphore(concurrency)

    async def _one_request(_index: int) -> None:
        nonlocal first_failure

        async with semaphore:
            lat, status, body = await send_request(task, language)

        err_kind, err_detail = check_response(task, language, status, body)
        if err_kind:
            error_kinds.append(err_kind)
            async with first_failure_lock:
                if first_failure is None and err_detail:
                    first_failure = err_detail
        else:
            latencies.append(lat)

    t_start = time.perf_counter()

    # Launch all tasks at once — the semaphore gates actual concurrency.
    tasks = [asyncio.create_task(_one_request(i)) for i in range(total)]
    # ``return_exceptions=True`` so a single crashed task (e.g. a bug in
    # ``send_request``) doesn't cancel all other in-flight requests.
    await asyncio.gather(*tasks, return_exceptions=True)

    elapsed = time.perf_counter() - t_start
    ok = len(latencies)
    failed = len(error_kinds)

    latencies.sort()
    return RunResult(
        task_name=task.name,
        language=language,
        concurrency=concurrency,
        total=total,
        ok=ok,
        failed=failed,
        elapsed_s=round(elapsed, 2),
        throughput=round(ok / elapsed, 1) if elapsed > 0 else 0,
        latency_p50=_percentile(latencies, 50),
        latency_p75=_percentile(latencies, 75),
        latency_p95=_percentile(latencies, 95),
        latency_p99=_percentile(latencies, 99),
        latency_mean=round(statistics.mean(latencies), 1) if latencies else 0,
        latency_min=round(min(latencies), 1) if latencies else 0,
        latency_max=round(max(latencies), 1) if latencies else 0,
        error_kinds=error_kinds,
        first_failure=first_failure,
    )


# ── Output helpers ───────────────────────────────────────────────────────

HEADER = "\033[1;36m"
GREEN = "\033[0;32m"
YELLOW = "\033[0;33m"
RED = "\033[0;31m"
BOLD = "\033[1m"
RESET = "\033[0m"
DIM = "\033[2m"


def _color_for_fail_rate(ok: int, failed: int) -> str:
    total = ok + failed
    if total == 0:
        return RED
    rate = failed / total
    if rate == 0:
        return GREEN
    if rate < 0.05:
        return YELLOW
    return RED


def _latency_color(ms: float, baseline: float) -> str:
    """Color latency relative to baseline. Green if close, yellow if high, red if extreme."""
    if baseline == 0 or ms == 0:
        return RESET
    ratio = ms / baseline
    if ratio < 1.5:
        return GREEN
    if ratio < 3.0:
        return YELLOW
    return RED


# ── Console report ───────────────────────────────────────────────────────


def print_report(results: list[RunResult], timestamp: str) -> None:
    """Print a formatted summary to stdout."""
    print()
    print(f"{HEADER}{'=' * 72}{RESET}")
    print(f"{HEADER}  Sandbox Stress Test Report{RESET}")
    print(f"{HEADER}  Target: {BASE_URL}{RESET}")
    print(f"{HEADER}  Date:   {timestamp}{RESET}")
    print(f"{HEADER}{'=' * 72}{RESET}")

    if not results:
        print(f"\n  {RED}No results collected.{RESET}")
        return

    # Group results by task + language
    groups: dict[tuple[str, str], list[RunResult]] = {}
    for r in results:
        key = (r.task_name, r.language)
        groups.setdefault(key, []).append(r)

    for (task_name, language), group in groups.items():
        group.sort(key=lambda r: r.concurrency)
        lang_label = {"python3": "Python", "javascript": "JavaScript"}.get(language, language)
        print(f"\n  {BOLD}── {task_name} ({lang_label}) ──{RESET}")
        print(f"  {DIM}{'Conc':>4} {'Total':>6} {'OK':>6} {'Fail':>5}  {'Dur(s)':>7}  "
              f"{'Thru/s':>7}  {'p50':>8} {'p95':>8} {'p99':>8} {'Mean':>8} {'Max':>8}{RESET}")

        for r in group:
            cf = _color_for_fail_rate(r.ok, r.failed)
            print(f"  {r.concurrency:>4} {r.total:>6} {r.ok:>6} "
                  f"{cf}{r.failed:>5}{RESET}  {r.elapsed_s:>7.2f}  "
                  f"{cf}{r.throughput:>7.1f}{RESET}  "
                  f"{r.latency_p50:>8.1f} {r.latency_p95:>8.1f} {r.latency_p99:>8.1f} "
                  f"{r.latency_mean:>8.1f} {r.latency_max:>8.1f}")

        # Show error breakdown and first failure detail
        for r in group:
            if r.failed > 0:
                # Count error kinds
                from collections import Counter
                kind_counts = Counter(r.error_kinds)
                parts = [f"{k} (x{c})" for k, c in kind_counts.most_common()]
                print(f"  {RED}  Failures [{r.concurrency}]: {', '.join(parts)}{RESET}")
                if r.first_failure:
                    # Indent the failure body
                    for line in r.first_failure.splitlines():
                        print(f"  {DIM}  │ {line}{RESET}")
                break  # show only the first concurrency level with failures per group

    # ── Cross-task summary table ──
    print(f"\n\n  {HEADER}{'=' * 72}{RESET}")
    print(f"  {BOLD}Summary: Throughput (req/s) by Task & Concurrency{RESET}")
    print(f"  {HEADER}{'=' * 72}{RESET}")

    concurrency_levels = sorted({r.concurrency for r in results})
    print(f"\n  {DIM}{'Task':<28}", end="")
    for c in concurrency_levels:
        print(f" {'c=' + str(c):>8}", end="")
    print(RESET)

    for (task_name, language), group in groups.items():
        lang_short = "py" if language == "python3" else "js"
        label = f"{task_name} ({lang_short})"
        print(f"  {label:<28}", end="")
        by_conc = {r.concurrency: r for r in group}
        for c in concurrency_levels:
            if c in by_conc:
                r = by_conc[c]
                cf = _color_for_fail_rate(r.ok, r.failed)
                print(f" {cf}{r.throughput:>8.1f}{RESET}", end="")
            else:
                print(f" {'--':>8}", end="")
        print()

    # ── Latency scaling summary ──
    print(f"\n\n  {HEADER}{'=' * 72}{RESET}")
    print(f"  {BOLD}Summary: p95 Latency (ms) by Task & Concurrency{RESET}")
    print(f"  {HEADER}{'=' * 72}{RESET}")

    print(f"\n  {DIM}{'Task':<28}", end="")
    for c in concurrency_levels:
        print(f" {'c=' + str(c):>8}", end="")
    print(RESET)

    for (task_name, language), group in groups.items():
        lang_short = "py" if language == "python3" else "js"
        label = f"{task_name} ({lang_short})"
        print(f"  {label:<28}", end="")
        by_conc = {r.concurrency: r for r in group}
        # Get baseline (c=1) p95 for color
        baseline = by_conc.get(1, RunResult("", "", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)).latency_p95
        for c in concurrency_levels:
            if c in by_conc:
                r = by_conc[c]
                lc = _latency_color(r.latency_p95, baseline)
                print(f" {lc}{r.latency_p95:>8.1f}{RESET}", end="")
            else:
                print(f" {'--':>8}", end="")
        print()

    print()


# ── JSON report ──────────────────────────────────────────────────────────


def _result_to_dict(r: RunResult) -> dict:
    return {
        "task_name": r.task_name,
        "language": r.language,
        "concurrency": r.concurrency,
        "total": r.total,
        "ok": r.ok,
        "failed": r.failed,
        "elapsed_s": r.elapsed_s,
        "throughput_req_per_s": r.throughput,
        "latency_ms": {
            "mean": r.latency_mean,
            "min": r.latency_min,
            "max": r.latency_max,
            "p50": r.latency_p50,
            "p75": r.latency_p75,
            "p95": r.latency_p95,
            "p99": r.latency_p99,
        },
        "error_kinds": r.error_kinds,
        "first_failure": r.first_failure,
    }


def save_json_report(results: list[RunResult], timestamp: str, path: Path) -> None:
    """Write machine-readable JSON report."""
    path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "meta": {
            "target": BASE_URL,
            "timestamp": timestamp,
            "total_runs": len(results),
        },
        "results": [_result_to_dict(r) for r in results],
    }
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  {DIM}JSON report saved to {path}{RESET}")


# ── Markdown report ──────────────────────────────────────────────────────


def save_markdown_report(results: list[RunResult], timestamp: str, path: Path) -> None:
    """Write human-readable Markdown report."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []

    lines.append(f"# Sandbox Stress Test Report")
    lines.append(f"")
    lines.append(f"- **Target:** `{BASE_URL}`")
    lines.append(f"- **Date:** {timestamp}")
    lines.append(f"- **Total runs:** {len(results)}")
    lines.append(f"")

    if not results:
        lines.append("> No results collected.")
        path.write_text("\n".join(lines), encoding="utf-8")
        return

    groups: dict[tuple[str, str], list[RunResult]] = {}
    for r in results:
        key = (r.task_name, r.language)
        groups.setdefault(key, []).append(r)

    lines.append(f"## Per-Task Results")
    lines.append(f"")

    for (task_name, language), group in groups.items():
        group.sort(key=lambda r: r.concurrency)
        lang_label = {"python3": "Python", "javascript": "JavaScript"}.get(language, language)
        lines.append(f"### {task_name} ({lang_label})")
        lines.append(f"")
        lines.append(
            f"| Concurrency | Total | OK | Failed | Duration (s) | Throughput (req/s) | p50 (ms) | p95 (ms) | p99 (ms) | Mean (ms) | Max (ms) |")
        lines.append(
            f"|------------:|------:|---:|-------:|-------------:|-------------------:|---------:|---------:|---------:|----------:|---------:|")
        for r in group:
            lines.append(
                f"| {r.concurrency} | {r.total} | {r.ok} | {r.failed} | {r.elapsed_s} | {r.throughput} | {r.latency_p50} | {r.latency_p95} | {r.latency_p99} | {r.latency_mean} | {r.latency_max} |")
        lines.append(f"")

        # Errors
        for r in group:
            if r.failed > 0:
                lines.append(f"**Failures:**")
                from collections import Counter
                kind_counts = Counter(r.error_kinds)
                parts = [f"`{k}` (×{c})" for k, c in kind_counts.most_common()]
                lines.append(f"- {', '.join(parts)}")
                if r.first_failure:
                    lines.append(f"")
                    lines.append(f"<details><summary>First failure detail</summary>")
                    lines.append(f"")
                    lines.append(f"```")
                    lines.append(r.first_failure)
                    lines.append(f"```")
                    lines.append(f"</details>")
                lines.append(f"")
                break

    # Throughput summary
    concurrency_levels = sorted({r.concurrency for r in results})
    lines.append(f"## Throughput Summary (req/s)")
    lines.append(f"")
    header = "| Task | " + " | ".join(f"c={c}" for c in concurrency_levels) + " |"
    lines.append(header)
    lines.append("|" + "---|" * (len(concurrency_levels) + 1))
    for (task_name, language), group in groups.items():
        lang_short = "py" if language == "python3" else "js"
        by_conc = {r.concurrency: r for r in group}
        row = f"| {task_name} ({lang_short}) | " + " | ".join(
            str(by_conc[c].throughput) if c in by_conc else "--" for c in concurrency_levels) + " |"
        lines.append(row)
    lines.append(f"")

    # Latency summary
    lines.append(f"## p95 Latency Summary (ms)")
    lines.append(f"")
    lines.append(header)
    lines.append("|" + "---|" * (len(concurrency_levels) + 1))
    for (task_name, language), group in groups.items():
        lang_short = "py" if language == "python3" else "js"
        by_conc = {r.concurrency: r for r in group}
        row = f"| {task_name} ({lang_short}) | " + " | ".join(
            str(by_conc[c].latency_p95) if c in by_conc else "--" for c in concurrency_levels) + " |"
        lines.append(row)
    lines.append(f"")

    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  {DIM}Markdown report saved to {path}{RESET}")


# ── Main ─────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sandbox stress test — multi-task, multi-concurrency",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python tests/stress_test.py                                   # all defaults
  python tests/stress_test.py --task computation --language python3
  python tests/stress_test.py --concurrency 20,50,100
  python tests/stress_test.py --total 500 --concurrency 50
  python tests/stress_test.py --markdown
  python tests/stress_test.py --warmup
        """,
    )
    parser.add_argument(
        "--task", "-t",
        nargs="+",
        choices=list(ALL_TASKS.keys()),
        default=list(ALL_TASKS.keys()),
        help="Task types to run (default: all)",
    )
    parser.add_argument(
        "--concurrency", "-c",
        type=lambda s: [int(x) for x in s.split(",")],
        default="1,20,50,100",
        help="Comma-separated concurrency levels (default: 1,20,50,100,200)",
    )
    parser.add_argument(
        "--language", "-l",
        choices=["python3", "javascript", "all"],
        default="all",
        help="Language to test (default: all)",
    )
    parser.add_argument(
        "--multiplier", "-m",
        type=int,
        default=5,
        help="Total requests = concurrency * multiplier, or --total overrides (default: 5)",
    )
    parser.add_argument(
        "--total",
        type=int,
        default=None,
        help="Fixed total requests per run (overrides --multiplier)",
    )
    parser.add_argument(
        "--warmup",
        action="store_true",
        help="Send warm-up requests before each test run",
    )
    parser.add_argument(
        "--markdown",
        action="store_true",
        help="Also generate a Markdown report file",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPORTS_DIR,
        help=f"Report output directory (default: {REPORTS_DIR})",
    )
    parser.add_argument(
        "--no-keep-alive",
        action="store_true",
        help="Force a fresh TCP connection for every request.  Each request hits "
             "the K8s LB independently, but adds a TCP 3-way handshake (~5-10 ms) "
             "per call.  Default OFF — connections are reused (keep-alive), cutting "
             "per-request latency by ~80%% for HTTP endpoints.",
    )
    parser.add_argument(
        "--max-connections",
        type=int,
        default=25,
        metavar="N",
        help="Max concurrent TCP connections in the pool (default: 50).  Capped to "
             "prevent ephemeral-port exhaustion on Windows.  Raise it for high-concurrency "
             "K8s tests (the server's listen backlog), but keep it below the OS port range.",
    )
    args = parser.parse_args()

    # ── Init HTTP client ──
    init_http_client(keep_alive=not args.no_keep_alive, max_connections=args.max_connections)

    # ── Run async test suite ──
    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        print(f"\n{YELLOW}Interrupted by user{RESET}")
        sys.exit(130)
    # asyncio.run() closes the event loop; _run() cleans up the client on the
    # same loop before returning (see finally block inside _run).


async def _run(args: argparse.Namespace) -> None:
    """Core test logic, runs inside ``asyncio.run()``."""

    try:
        # ── Health check ──
        print(f"Checking {BASE_URL}/health ... ", end="", flush=True)
        try:
            resp = await _http_client.get("/health")
            data = resp.json()
            if data.get("ok"):
                print(f"{GREEN}OK{RESET} (workers={data.get('workers', '?')})")
            else:
                print(f"{RED}FAIL{RESET}: server returned ok=false")
                sys.exit(1)
        except Exception as e:
            print(f"{RED}FAIL{RESET}: {e}")
            sys.exit(1)

        # ── Determine languages ──
        languages: list[str] = (
            ["python3", "javascript"] if args.language == "all" else [args.language]
        )

        # ── Determine tasks ──
        tasks = [ALL_TASKS[name] for name in args.task]

        # ── Run tests ──
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        timestamp_file = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

        total_combinations = len(tasks) * len(languages) * len(args.concurrency)
        print(f"\n{BOLD}Test plan:{RESET} {len(tasks)} task(s) × {len(languages)} language(s) × "
              f"{len(args.concurrency)} concurrency level(s) = {total_combinations} run(s)")
        print(f"Total requests formula: concurrency × {args.multiplier} (min 10)")
        print()

        all_results: list[RunResult] = []
        run_num = 0

        for task in tasks:
            for language in languages:
                if args.warmup:
                    print(f"  {DIM}Warming up {task.name} ({language})...{RESET}", end=" ", flush=True)
                    await warmup(task, language, n=3)
                    print(f"{GREEN}done{RESET}")

                for concurrency in args.concurrency:
                    run_num += 1
                    total_requests = args.total if args.total is not None else max(concurrency * args.multiplier, 10)

                    print(f"  [{run_num}/{total_combinations}] {task.name} ({language}) "
                          f"c={concurrency} n={total_requests} ... ", end="", flush=True)

                    result = await run_single(task, language, total_requests, concurrency)
                    all_results.append(result)

                    cf = _color_for_fail_rate(result.ok, result.failed)
                    print(f"{cf}{result.ok} ok, {result.failed} fail, "
                          f"{result.elapsed_s}s, {result.throughput} req/s, "
                          f"p95={result.latency_p95}ms{RESET}")

        # ── Generate reports ──
        print_report(all_results, timestamp)

        json_path = args.output_dir / f"report_{timestamp_file}.json"
        save_json_report(all_results, timestamp, json_path)

        if args.markdown:
            md_path = args.output_dir / f"report_{timestamp_file}.md"
            save_markdown_report(all_results, timestamp, md_path)

        # ── Exit code ──
        total_failed = sum(r.failed for r in all_results)
        if total_failed > 0:
            print(f"{RED}{total_failed} total failures across all runs{RESET}")
            sys.exit(1)
    finally:
        # Close on the SAME event loop the client was created on.
        await close_http_client()


if __name__ == "__main__":
    main()
