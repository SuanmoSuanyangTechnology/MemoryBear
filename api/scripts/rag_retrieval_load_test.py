#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "locust==2.45.0",
#   "PyYAML==6.0.2",
# ]
# ///
"""Standalone Locust runner for the MemoryBear retrieval HTTP endpoint.

Run with ``uv run rag_retrieval_load_test.py --help``. The parent process
validates inputs and starts a headless Locust child using this same file.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from locust import HttpUser, LoadTestShape, events, task


SCHEMA_VERSION = 1
TOKEN_ENV = "RAG_EVAL_JWT_TOKEN"
CHILD_DATASET_ENV = "RAG_LOAD_DATASET"
CHILD_CONFIG_ENV = "RAG_LOAD_CONFIG"
CHILD_OUTPUT_ENV = "RAG_LOAD_OUTPUT_DIR"
CHILD_BASE_URL_ENV = "RAG_LOAD_BASE_URL"
RETRIEVAL_FIELDS = {
    "retrieve_type",
    "top_k",
    "top_n",
    "similarity_threshold",
    "vector_similarity_weight",
    "rerank_score_threshold",
}
TARGET_FIELDS = {"kb_ids", "file_names_filter"}


class LoadConfigError(RuntimeError):
    """Raised when the load test cannot start with valid inputs."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        if path.suffix.lower() == ".json":
            value = json.load(handle)
        else:
            value = yaml.safe_load(handle)
    if not isinstance(value, dict):
        raise LoadConfigError(f"Expected object in config {path}")
    return value


def read_dataset(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise LoadConfigError(f"Invalid JSONL at {path}:{line_number}: {exc}") from exc
            if not isinstance(row, dict):
                raise LoadConfigError(f"Expected object at {path}:{line_number}")
            rows.append(row)
    if not rows:
        raise LoadConfigError("Dataset is empty")
    return rows


def validate_runtime(config: dict[str, Any], samples: list[dict[str, Any]]) -> None:
    retrieval = config.get("retrieval") or {}
    unknown_retrieval = set(retrieval) - RETRIEVAL_FIELDS
    if unknown_retrieval:
        raise LoadConfigError(f"Unknown retrieval config fields: {sorted(unknown_retrieval)}")
    top_k = int(retrieval.get("top_k", 10))
    top_n = int(retrieval.get("top_n", 20))
    if not 1 <= top_k <= 100 or not 1 <= top_n <= 100 or top_n < top_k:
        raise LoadConfigError("retrieval top_k/top_n must be within 1..100 and top_n >= top_k")
    load = config.get("load") or {}
    profile = str(load.get("profile") or "smoke")
    if profile not in {"smoke", "baseline", "staircase", "soak"}:
        raise LoadConfigError(f"Unsupported load profile: {profile}")
    if profile == "staircase":
        stages = load.get("stages")
        if not isinstance(stages, list) or not stages:
            raise LoadConfigError("staircase profile requires at least one stage")
        for index, stage in enumerate(stages, start=1):
            if not isinstance(stage, dict):
                raise LoadConfigError(f"stage[{index}] must be an object")
            if int(stage.get("users", 0)) < 1 or float(stage.get("spawn_rate", 0)) <= 0:
                raise LoadConfigError(f"stage[{index}] users/spawn_rate must be positive")
            if float(stage.get("duration_seconds", 0)) <= 0:
                raise LoadConfigError(f"stage[{index}] duration_seconds must be positive")
    for index, sample in enumerate(samples, start=1):
        if not str(sample.get("query") or "").strip():
            raise LoadConfigError(f"sample[{index}] has an empty query")
        target = sample.get("target")
        if not isinstance(target, dict) or not target.get("kb_ids"):
            raise LoadConfigError(f"sample[{index}] target.kb_ids must be non-empty")
        unknown_target = set(target) - TARGET_FIELDS
        if unknown_target:
            raise LoadConfigError(f"sample[{index}] has unknown target fields: {sorted(unknown_target)}")
        weight = sample.get("load_weight", 1)
        if not isinstance(weight, int) or weight < 1:
            raise LoadConfigError(f"sample[{index}] load_weight must be a positive integer")


def safe_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


class Runtime:
    """Immutable runtime inputs loaded by the Locust child process."""

    def __init__(self, dataset_path: Path, config_path: Path, output_dir: Path, base_url: str):
        self.dataset_path = dataset_path
        self.config_path = config_path
        self.output_dir = output_dir
        self.base_url = base_url.rstrip("/")
        self.samples = read_dataset(dataset_path)
        self.config = read_config(config_path)
        validate_runtime(self.config, self.samples)
        self.retrieval = dict(self.config.get("retrieval") or {})
        self.load = dict(self.config.get("load") or {})
        self.gates = dict(self.config.get("gates") or {})
        self.profile = str(self.load.get("profile") or "smoke")
        self.endpoint = str(self.config.get("endpoint") or "/api/chunks/retrieval")
        if self.endpoint != "/api/chunks/retrieval":
            raise LoadConfigError("Only /api/chunks/retrieval is supported in the first release")
        seed = int(self.config.get("seed", 20260715))
        self.random = random.Random(seed)
        self.weights = [int(sample.get("load_weight", 1)) for sample in self.samples]

    def choose_sample(self) -> dict[str, Any]:
        return self.random.choices(self.samples, weights=self.weights, k=1)[0]


def load_child_runtime() -> Runtime | None:
    dataset = os.getenv(CHILD_DATASET_ENV)
    config = os.getenv(CHILD_CONFIG_ENV)
    output = os.getenv(CHILD_OUTPUT_ENV)
    base_url = os.getenv(CHILD_BASE_URL_ENV)
    if not all((dataset, config, output, base_url)):
        return None
    return Runtime(Path(dataset), Path(config), Path(output), base_url)


RUNTIME = load_child_runtime()


class RetrievalUser(HttpUser):
    """Locust user that calls only the deployed retrieval HTTP contract."""

    abstract = RUNTIME is None
    host = RUNTIME.base_url if RUNTIME else None

    def on_start(self) -> None:
        token = os.getenv(TOKEN_ENV, "").strip()
        if not token:
            raise LoadConfigError(f"Missing authentication token in {TOKEN_ENV}")
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def wait_time(self) -> float:
        return float(RUNTIME.load.get("wait_seconds", 0.0)) if RUNTIME else 0.0

    @task
    def retrieve(self) -> None:
        if RUNTIME is None:
            return
        sample = RUNTIME.choose_sample()
        payload = {
            "query": sample["query"],
            **sample["target"],
            **RUNTIME.retrieval,
        }
        request_name = "/api/chunks/retrieval|{}|{}".format(
            sample.get("corpus_mode", "unknown"),
            RUNTIME.retrieval.get("retrieve_type", "default"),
        )
        timeout = float(RUNTIME.load.get("request_timeout_seconds", 60.0))
        with self.client.post(
            RUNTIME.endpoint,
            json=payload,
            headers=self.headers,
            name=request_name,
            timeout=timeout,
            catch_response=True,
        ) as response:
            if not 200 <= response.status_code < 300:
                response.failure(f"http_{response.status_code}")
                return
            try:
                envelope = response.json()
            except ValueError:
                response.failure("invalid_json")
                return
            if not isinstance(envelope, dict):
                response.failure("invalid_envelope")
                return
            if envelope.get("code") != 0:
                response.failure("business_code_nonzero")
                return
            if not isinstance(envelope.get("data"), list):
                response.failure("invalid_data_schema")
                return
            if sample.get("corpus_mode") in {"single_document_kb", "single_document_filter"}:
                allowed = {
                    value.split(":", 1)[-1]
                    for value in sample.get("gold_document_ids") or []
                }
                returned = {
                    str((item.get("metadata") or {}).get("document_id") or "")
                    for item in envelope["data"]
                    if isinstance(item, dict)
                }
                returned.discard("")
                if allowed and not returned <= allowed:
                    response.failure("single_scope_violation")


if RUNTIME is not None and RUNTIME.profile == "staircase":

    class ConfiguredStaircaseShape(LoadTestShape):
        """Load shape whose cumulative stages are supplied by the external config."""

        def tick(self) -> tuple[int, float] | None:
            elapsed = self.get_run_time()
            cumulative = 0.0
            stages = RUNTIME.load.get("stages") or []
            for stage in stages:
                cumulative += float(stage.get("duration_seconds", 0))
                if elapsed < cumulative:
                    return int(stage["users"]), float(stage.get("spawn_rate", 1))
            return None


def build_summary(environment: Any) -> tuple[dict[str, Any], list[str]]:
    total = environment.stats.total
    request_count = int(total.num_requests)
    failure_count = int(total.num_failures)
    failure_ratio = failure_count / request_count if request_count else 1.0
    summary = {
        "schema_version": SCHEMA_VERSION,
        "created_at": utc_now(),
        "profile": RUNTIME.profile if RUNTIME else None,
        "request_count": request_count,
        "success_count": request_count - failure_count,
        "failure_count": failure_count,
        "failure_ratio": failure_ratio,
        "achieved_rps": float(total.total_rps),
        "latency_ms": {
            "p50": total.get_response_time_percentile(0.50),
            "p90": total.get_response_time_percentile(0.90),
            "p95": total.get_response_time_percentile(0.95),
            "p99": total.get_response_time_percentile(0.99),
            "max": total.max_response_time,
        },
    }
    failures: list[str] = []
    gates = RUNTIME.gates if RUNTIME else {}
    if request_count < int(gates.get("min_requests", 1)):
        failures.append("min_requests")
    if failure_ratio > float(gates.get("max_failure_ratio", 1.0)):
        failures.append("max_failure_ratio")
    p95 = summary["latency_ms"]["p95"]
    if gates.get("max_p95_ms") is not None and (p95 is None or p95 > float(gates["max_p95_ms"])):
        failures.append("max_p95_ms")
    if gates.get("min_achieved_rps") is not None and summary["achieved_rps"] < float(gates["min_achieved_rps"]):
        failures.append("min_achieved_rps")
    summary["gate_failures"] = failures
    summary["passed"] = not failures
    return summary, failures


@events.quitting.add_listener
def on_quitting(environment: Any, **_: Any) -> None:
    if RUNTIME is None:
        return
    summary, failures = build_summary(environment)
    safe_write_json(RUNTIME.output_dir / "summary.json", summary)
    environment.process_exit_code = 2 if failures else 0


def default_output_dir() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path("rag_load_results") / stamp


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--base-url", default=os.getenv("RAG_EVAL_BASE_URL"), required=not os.getenv("RAG_EVAL_BASE_URL"))
    parser.add_argument("--output-dir", type=Path, default=None)
    return parser


def run_parent(args: argparse.Namespace) -> int:
    token = os.getenv(TOKEN_ENV, "").strip()
    if not token:
        raise LoadConfigError(f"Missing authentication token in {TOKEN_ENV}")
    dataset = args.dataset.resolve()
    config_path = args.config.resolve()
    samples = read_dataset(dataset)
    config = read_config(config_path)
    validate_runtime(config, samples)
    output_dir = (args.output_dir or default_output_dir()).resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    runtime = Runtime(dataset, config_path, output_dir, args.base_url)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "created_at": utc_now(),
        "dataset": str(dataset),
        "config": str(config_path),
        "base_url": args.base_url.rstrip("/"),
        "case_count": len(samples),
        "profile": runtime.profile,
        "retrieval": runtime.retrieval,
        "load": runtime.load,
        "gates": runtime.gates,
    }
    safe_write_json(output_dir / "manifest.json", manifest)

    env = os.environ.copy()
    env.update(
        {
            CHILD_DATASET_ENV: str(dataset),
            CHILD_CONFIG_ENV: str(config_path),
            CHILD_OUTPUT_ENV: str(output_dir),
            CHILD_BASE_URL_ENV: args.base_url.rstrip("/"),
        }
    )
    stats_prefix = output_dir / "locust"
    command = [
        sys.executable,
        "-m",
        "locust",
        "-f",
        str(Path(__file__).resolve()),
        "--headless",
        "-H",
        args.base_url.rstrip("/"),
        "--csv",
        str(stats_prefix),
        "--csv-full-history",
        "--html",
        str(output_dir / "report.html"),
        "--json-file",
        str(output_dir / "locust"),
    ]
    if runtime.profile != "staircase":
        command.extend(
            [
                "--users",
                str(int(runtime.load.get("users", 1))),
                "--spawn-rate",
                str(float(runtime.load.get("spawn_rate", 1))),
                "--run-time",
                str(runtime.load.get("run_time", "30s")),
            ]
        )
    completed = subprocess.run(command, env=env, check=False)
    return int(completed.returncode)


def main() -> int:
    args = build_parser().parse_args()
    try:
        return run_parent(args)
    except (LoadConfigError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
