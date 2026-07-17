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
import csv
import html
import json
import os
import random
import re
import statistics
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
GATE_FIELDS = {
    "min_requests",
    "max_failure_ratio",
    "max_p95_ms",
    "min_achieved_rps",
}


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
                raise LoadConfigError(
                    f"Invalid JSONL at {path}:{line_number}: {exc}"
                ) from exc
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
        raise LoadConfigError(
            f"Unknown retrieval config fields: {sorted(unknown_retrieval)}"
        )
    top_k = int(retrieval.get("top_k", 10))
    top_n = int(retrieval.get("top_n", 20))
    if not 1 <= top_k <= 100 or not 1 <= top_n <= 100 or top_n < top_k:
        raise LoadConfigError(
            "retrieval top_k/top_n must be within 1..100 and top_n >= top_k"
        )
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
                raise LoadConfigError(
                    f"stage[{index}] users/spawn_rate must be positive"
                )
            if float(stage.get("duration_seconds", 0)) <= 0:
                raise LoadConfigError(
                    f"stage[{index}] duration_seconds must be positive"
                )
    gates = config.get("gates") or {}
    if not isinstance(gates, dict):
        raise LoadConfigError("gates must be an object")
    unknown_gates = set(gates) - GATE_FIELDS
    if unknown_gates:
        raise LoadConfigError(f"Unknown performance gates: {sorted(unknown_gates)}")
    for index, sample in enumerate(samples, start=1):
        if not str(sample.get("query") or "").strip():
            raise LoadConfigError(f"sample[{index}] has an empty query")
        target = sample.get("target")
        if not isinstance(target, dict) or not target.get("kb_ids"):
            raise LoadConfigError(f"sample[{index}] target.kb_ids must be non-empty")
        unknown_target = set(target) - TARGET_FIELDS
        if unknown_target:
            raise LoadConfigError(
                f"sample[{index}] has unknown target fields: {sorted(unknown_target)}"
            )
        weight = sample.get("load_weight", 1)
        if not isinstance(weight, int) or weight < 1:
            raise LoadConfigError(
                f"sample[{index}] load_weight must be a positive integer"
            )


def safe_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def read_json_object(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise LoadConfigError(f"Expected object in {path}")
    return value


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def html_escape(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def parse_float(value: Any) -> float | None:
    if value in (None, "", "N/A"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_int(value: Any) -> int:
    parsed = parse_float(value)
    return int(parsed) if parsed is not None else 0


def format_number(value: Any, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{float(value):.{digits}f}"


def format_percent(value: Any) -> str:
    if value is None:
        return "—"
    return f"{float(value) * 100:.2f}%"


def summary_from_stats(output_dir: Path) -> dict[str, Any] | None:
    rows = read_csv_rows(output_dir / "locust_stats.csv")
    aggregate = next(
        (row for row in reversed(rows) if row.get("Name") == "Aggregated"), None
    )
    if not aggregate:
        return None
    request_count = parse_int(aggregate.get("Request Count"))
    failure_count = parse_int(aggregate.get("Failure Count"))
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": utc_now(),
        "profile": None,
        "request_count": request_count,
        "success_count": request_count - failure_count,
        "failure_count": failure_count,
        "failure_ratio": failure_count / request_count if request_count else 1.0,
        "achieved_rps": parse_float(aggregate.get("Requests/s")) or 0.0,
        "latency_ms": {
            "p50": parse_float(aggregate.get("50%")),
            "p90": parse_float(aggregate.get("90%")),
            "p95": parse_float(aggregate.get("95%")),
            "p99": parse_float(aggregate.get("99%")),
            "max": parse_float(aggregate.get("Max Response Time")),
        },
        "gate_failures": [],
        "passed": None,
    }


def stage_statistics(
    output_dir: Path, manifest: dict[str, Any]
) -> list[dict[str, Any]]:
    if manifest.get("profile") != "staircase":
        return []
    rows = [
        row
        for row in read_csv_rows(output_dir / "locust_stats_history.csv")
        if row.get("Name") == "Aggregated" and parse_int(row.get("User Count")) > 0
    ]
    expected_users = [
        int(stage.get("users", 0))
        for stage in (manifest.get("load") or {}).get("stages") or []
        if int(stage.get("users", 0)) > 0
    ]
    users = list(
        dict.fromkeys(
            expected_users or sorted({parse_int(row.get("User Count")) for row in rows})
        )
    )
    results = []
    for user_count in users:
        group = [row for row in rows if parse_int(row.get("User Count")) == user_count]
        if not group:
            results.append(
                {
                    "users": user_count,
                    "samples": 0,
                    "rps": None,
                    "p50": None,
                    "p95": None,
                }
            )
            continue

        def median_field(field: str) -> float | None:
            values = [
                value
                for row in group
                if (value := parse_float(row.get(field))) is not None
            ]
            return float(statistics.median(values)) if values else None

        results.append(
            {
                "users": user_count,
                "samples": len(group),
                "rps": median_field("Requests/s"),
                "p50": median_field("50%"),
                "p95": median_field("95%"),
            }
        )
    return results


def failure_statistics(output_dir: Path) -> list[dict[str, Any]]:
    grouped: dict[str, int] = {}
    for row in read_csv_rows(output_dir / "locust_failures.csv"):
        raw_error = str(row.get("Error") or "unknown")
        match = re.search(
            r"(?:http_\d+|invalid_[a-z_]+|business_code_nonzero|single_scope_violation)",
            raw_error,
        )
        error = match.group(0) if match else raw_error
        grouped[error] = grouped.get(error, 0) + parse_int(row.get("Occurrences"))
    return [
        {"error": error, "count": count} for error, count in sorted(grouped.items())
    ]


GATE_LABELS = {
    "min_requests": "最小请求数",
    "max_failure_ratio": "最大失败率",
    "max_p95_ms": "p95 最大耗时",
    "min_achieved_rps": "最小实际 RPS",
}

PROFILE_LABELS = {
    "smoke": "冒烟测试",
    "baseline": "基线测试",
    "staircase": "阶梯加压",
    "soak": "稳定性压测",
}


def gate_actual_value(name: str, summary: dict[str, Any]) -> Any:
    if name == "min_requests":
        return summary.get("request_count")
    if name == "max_failure_ratio":
        return summary.get("failure_ratio")
    if name == "max_p95_ms":
        return (summary.get("latency_ms") or {}).get("p95")
    if name == "min_achieved_rps":
        return summary.get("achieved_rps")
    return None


def format_gate_value(name: str, value: Any) -> str:
    if name == "max_failure_ratio":
        return format_percent(value)
    if name == "max_p95_ms":
        return f"{format_number(value, 1)} ms"
    if name == "min_achieved_rps":
        return format_number(value, 2)
    return html_escape(value)


def preserve_locust_report(output_dir: Path) -> Path | None:
    report = output_dir / "report.html"
    locust_report = output_dir / "locust-report.html"
    if locust_report.exists():
        return locust_report
    if report.exists():
        prefix = report.read_text(encoding="utf-8", errors="replace")[:2000]
        if "<title>Locust" in prefix:
            report.replace(locust_report)
            return locust_report
    return None


def render_performance_report(output_dir: Path) -> tuple[Path, bool]:
    output_dir = output_dir.resolve()
    manifest_path = output_dir / "manifest.json"
    if not manifest_path.exists():
        raise LoadConfigError(f"Missing manifest.json in {output_dir}")
    manifest = read_json_object(manifest_path)
    summary_path = output_dir / "summary.json"
    completed_run = summary_path.exists()
    summary = (
        read_json_object(summary_path)
        if completed_run
        else summary_from_stats(output_dir)
    )
    if summary is None:
        raise LoadConfigError(
            f"Missing summary.json and usable locust_stats.csv in {output_dir}"
        )

    locust_report = preserve_locust_report(output_dir)
    failures = failure_statistics(output_dir)
    stages = stage_statistics(output_dir, manifest)
    passed = summary.get("passed") if completed_run else None
    if not completed_run:
        status_text, status_class = "运行未完整结束", "warn"
    elif passed:
        status_text, status_class = "性能门禁通过", "good"
    else:
        status_text, status_class = "性能门禁未通过", "bad"

    gate_failures = set(summary.get("gate_failures") or [])
    gate_rows = []
    for name, expected in (manifest.get("gates") or {}).items():
        actual = gate_actual_value(name, summary)
        if not completed_run:
            result = "未判定"
            result_class = "warn-text"
        elif name in gate_failures:
            result = "未通过"
            result_class = "bad-text"
        else:
            result = "通过"
            result_class = "good-text"
        gate_rows.append(
            "<tr>"
            f"<td>{html_escape(GATE_LABELS.get(name, name))}</td>"
            f"<td>{format_gate_value(name, expected)}</td>"
            f"<td>{format_gate_value(name, actual)}</td>"
            f'<td class="{result_class}">{result}</td>'
            "</tr>"
        )
    if not gate_rows:
        gate_rows.append('<tr><td colspan="4">本次运行未配置性能门禁</td></tr>')

    failure_rows = [
        f"<tr><td><code>{html_escape(item['error'])}</code></td><td>{item['count']}</td></tr>"
        for item in failures
    ] or ['<tr><td colspan="2">未记录请求失败</td></tr>']
    stage_rows = [
        "<tr>"
        f"<td>{item['users']}</td><td>{item['samples']}</td>"
        f"<td>{format_number(item['rps'], 2)}</td>"
        f"<td>{format_number(item['p50'], 1)} ms</td>"
        f"<td>{format_number(item['p95'], 1)} ms</td>"
        "</tr>"
        for item in stages
    ]

    load = manifest.get("load") or {}
    retrieval = manifest.get("retrieval") or {}
    latency = summary.get("latency_ms") or {}
    profile = str(manifest.get("profile") or summary.get("profile") or "unknown")
    raw_report_link = (
        '<a href="locust-report.html">Locust 原始英文报告</a>'
        if locust_report
        else "Locust 原始报告未生成"
    )
    incomplete_note = ""
    if not completed_run:
        incomplete_note = (
            '<p class="notice">本次运行没有生成 <code>summary.json</code>，'
            "页面中的请求与延迟数据来自 <code>locust_stats.csv</code>，不判定性能门禁。</p>"
        )
    stage_section = ""
    if stages:
        stage_section = f"""<section><h2>阶梯分档结果</h2>
<p class="muted">RPS、p50 和 p95 取该并发档位秒级历史样本的中位数，避免把不同并发档聚合成一个值。</p>
<div class="table-wrap"><table><thead><tr><th>并发用户</th><th>历史样本数</th><th>RPS</th><th>p50</th><th>p95</th></tr></thead>
<tbody>{"".join(stage_rows)}</tbody></table></div></section>"""

    report = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>知识库检索性能测试报告</title>
<style>
:root {{ color-scheme:light; --ink:#172033; --muted:#637083; --line:#dce3ed; --panel:#f7f9fc; --good:#137a4b; --bad:#b42318; --warn:#9a6700; }}
* {{ box-sizing:border-box; }} body {{ margin:0; background:#eef2f7; color:var(--ink); font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; }}
main {{ width:min(1200px,calc(100% - 32px)); margin:32px auto; }} header,section {{ background:#fff; border:1px solid var(--line); border-radius:14px; padding:22px; margin-bottom:16px; box-shadow:0 6px 24px rgba(25,41,72,.05); }}
h1 {{ margin:0 0 8px; font-size:28px; }} h2 {{ margin:0 0 14px; font-size:19px; }} p {{ margin:6px 0; }} .muted {{ color:var(--muted); }}
.status {{ display:inline-block; padding:4px 10px; border-radius:999px; font-weight:700; }} .status.good {{ color:var(--good); background:#e8f7ef; }} .status.bad {{ color:var(--bad); background:#ffebe9; }} .status.warn {{ color:var(--warn); background:#fff4ce; }}
.cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; margin-top:18px; }} .card {{ background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:14px; }} .card b {{ display:block; font-size:22px; margin-top:3px; }}
.params {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:8px 18px; }} .params div {{ border-bottom:1px dashed var(--line); padding:6px 0; }}
.table-wrap {{ overflow:auto; }} table {{ width:100%; border-collapse:collapse; white-space:nowrap; }} th,td {{ border-bottom:1px solid var(--line); padding:10px 12px; text-align:right; }} th:first-child,td:first-child {{ text-align:left; }} thead th {{ background:var(--panel); color:#3c4960; }}
.good-text {{ color:var(--good); font-weight:650; }} .bad-text {{ color:var(--bad); font-weight:650; }} .warn-text {{ color:var(--warn); font-weight:650; }}
.notice {{ border-left:4px solid var(--warn); padding:10px 14px; background:#fff9e8; }} code {{ font-family:"SFMono-Regular",Consolas,monospace; font-size:12px; }} a {{ color:#3157d5; }}
</style>
</head><body><main>
<header><span class="status {status_class}">{status_text}</span><h1>知识库检索性能测试报告</h1>
<p class="muted">测试类型：{html_escape(PROFILE_LABELS.get(profile, profile))}，开始记录时间：{html_escape(manifest.get("created_at"))}</p>
{incomplete_note}
<div class="cards">
  <div class="card">请求数<b>{int(summary.get("request_count") or 0)}</b></div>
  <div class="card">成功数<b>{int(summary.get("success_count") or 0)}</b></div>
  <div class="card">失败数<b>{int(summary.get("failure_count") or 0)}</b></div>
  <div class="card">失败率<b>{format_percent(summary.get("failure_ratio"))}</b></div>
  <div class="card">实际 RPS<b>{format_number(summary.get("achieved_rps"), 2)}</b></div>
</div></header>
<section><h2>延迟指标</h2><div class="cards">
  <div class="card">p50<b>{format_number(latency.get("p50"), 1)} ms</b></div>
  <div class="card">p90<b>{format_number(latency.get("p90"), 1)} ms</b></div>
  <div class="card">p95<b>{format_number(latency.get("p95"), 1)} ms</b></div>
  <div class="card">p99<b>{format_number(latency.get("p99"), 1)} ms</b></div>
  <div class="card">最大耗时<b>{format_number(latency.get("max"), 1)} ms</b></div>
</div></section>
<section><h2>运行参数</h2><div class="params">
  <div>目标环境：{html_escape(manifest.get("base_url"))}</div><div>数据集 case：{html_escape(manifest.get("case_count"))}</div>
  <div>负载 profile：{html_escape(profile)}</div><div>并发用户：{html_escape(load.get("users") or "阶梯分档")}</div>
  <div>运行时间：{html_escape(load.get("run_time") or "见阶梯配置")}</div><div>请求超时：{html_escape(load.get("request_timeout_seconds") or 60)} s</div>
  <div>检索方式：{html_escape(retrieval.get("retrieve_type") or "default")}</div><div>top_k / top_n：{html_escape(retrieval.get("top_k") or 10)} / {html_escape(retrieval.get("top_n") or 20)}</div>
  <div>相似度阈值：{html_escape(retrieval.get("similarity_threshold"))}</div><div>向量权重：{html_escape(retrieval.get("vector_similarity_weight"))}</div>
</div></section>
<section><h2>性能门禁</h2><div class="table-wrap"><table><thead><tr><th>门禁</th><th>要求</th><th>实际</th><th>结果</th></tr></thead><tbody>{"".join(gate_rows)}</tbody></table></div></section>
{stage_section}
<section><h2>请求失败</h2><div class="table-wrap"><table><thead><tr><th>失败类型</th><th>次数</th></tr></thead><tbody>{"".join(failure_rows)}</tbody></table></div></section>
<section><h2>原始产物</h2><p>{raw_report_link}</p><p class="muted">机器可读结果仍保存在 <code>summary.json</code>、<code>manifest.json</code> 和 <code>locust_*.csv</code>中。</p></section>
<section><h2>口径说明</h2><p>RPS 表示每秒实际完成的请求数。p95 表示 95% 的已完成请求耗时不超过该值。性能成功只说明 HTTP 和返回结构正常，召回内容是否正确由召回效果评测脚本判定。</p></section>
</main></body></html>
"""
    report_path = output_dir / "report.html"
    report_path.write_text(report, encoding="utf-8")
    return report_path, completed_run


class Runtime:
    """Immutable runtime inputs loaded by the Locust child process."""

    def __init__(
        self, dataset_path: Path, config_path: Path, output_dir: Path, base_url: str
    ):
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
            raise LoadConfigError(
                "Only /api/chunks/retrieval is supported in the first release"
            )
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
            if sample.get("corpus_mode") in {
                "single_document_kb",
                "single_document_filter",
            }:
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
    if gates.get("max_p95_ms") is not None and (
        p95 is None or p95 > float(gates["max_p95_ms"])
    ):
        failures.append("max_p95_ms")
    if gates.get("min_achieved_rps") is not None and summary["achieved_rps"] < float(
        gates["min_achieved_rps"]
    ):
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
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--base-url", default=os.getenv("RAG_EVAL_BASE_URL"))
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument(
        "--render-report-dir",
        type=Path,
        help="Render a Chinese report from an existing performance run without starting Locust",
    )
    return parser


def run_parent(args: argparse.Namespace) -> int:
    missing = [
        name for name in ("dataset", "config", "base_url") if not getattr(args, name)
    ]
    if missing:
        raise LoadConfigError(
            f"Missing required run arguments: {', '.join('--' + name.replace('_', '-') for name in missing)}"
        )
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
        str(output_dir / "locust-report.html"),
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
    report_path, _ = render_performance_report(output_dir)
    print(
        json.dumps(
            {"report": str(report_path), "locust_exit_code": completed.returncode},
            ensure_ascii=False,
        )
    )
    return int(completed.returncode)


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.render_report_dir:
            if any((args.dataset, args.config, args.output_dir)):
                raise LoadConfigError(
                    "--render-report-dir cannot be combined with --dataset, --config, or --output-dir"
                )
            report_path, completed_run = render_performance_report(
                args.render_report_dir
            )
            print(
                json.dumps(
                    {"report": str(report_path), "completed_run": completed_run},
                    ensure_ascii=False,
                )
            )
            return 0
        return run_parent(args)
    except (LoadConfigError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
