#!/usr/bin/env python
"""Defect battery, absolute expectation check: assert the fixed wire
semantics (error codes, messages, kept/stored sub_agents, statistics counts)
against a recording. Cross-plane equivalence of the rest is covered by
``parity_compare.py`` over the same recordings.

Run (from the enterprise repo root, stdlib only):
  uv run python core/application-service/scripts/p1_verify/defect_expect_check.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

DEFAULT_MONOLITH = "/tmp/app_split_defect_monolith.json"
DEFAULT_APP_SERVICE = "/tmp/app_split_defect_app_service.json"

NO_CONFIG_MSG = "多 Agent 配置 不存在"
EMPTY_SUB_AGENTS_MSG = "多 Agent 配置无子 Agent，无法进行路由测试"
FOREIGN_APP_MSG = "应用不可访问"


def same(actual, expected) -> bool:
    """Recordings stringify small integers; compare scalars as text."""
    if isinstance(expected, dict) and isinstance(actual, dict):
        return set(actual) == set(expected) and all(
            same(actual[key], value) for key, value in expected.items()
        )
    if isinstance(expected, list) and isinstance(actual, list):
        return len(actual) == len(expected) and all(
            same(left, right) for left, right in zip(actual, expected, strict=False)
        )
    return str(actual) == str(expected)


def envelope(step: dict) -> dict:
    return step.get("data") or {}


def payload(step: dict) -> dict:
    return envelope(step).get("data") or {}


def check(record: dict, problems: list[str]) -> None:
    side = record.get("side", "?")
    steps = {step["step"]: step for step in record["steps"]}

    def require(condition: bool, label: str) -> None:
        if not condition:
            problems.append(f"[{side}] {label}")

    step = steps["00_db_tool_baseline"]
    require(same(step["data"], [0]), f"00 baseline not empty: {step['data']!r}")

    step = steps["02_routing_without_config"]
    require(step["status"] == 400, f"02 status {step['status']} != 400")
    require(same(envelope(step).get("code"), 4006), f"02 code != 4006: {envelope(step)}")
    require(envelope(step).get("msg") == NO_CONFIG_MSG, f"02 msg: {envelope(step).get('msg')!r}")

    step = steps["03_put_create_without_sub_agents"]
    require(step["status"] == 200, f"03 status {step['status']} != 200")
    require(same(envelope(step).get("code"), 0), f"03 code: {envelope(step).get('code')}")
    require(
        payload(step).get("sub_agents") == [],
        f"03 sub_agents: {payload(step).get('sub_agents')!r}",
    )
    require(
        payload(step).get("master_agent_name") == "M",
        f"03 master_agent_name: {payload(step).get('master_agent_name')!r}",
    )

    step = steps["04_db_sub_agents_after_create"]
    require(step["data"] == [[]], f"04 stored sub_agents: {step['data']!r}")

    step = steps["05_routing_empty_sub_agents"]
    require(step["status"] == 400, f"05 status {step['status']} != 400")
    require(same(envelope(step).get("code"), 6005), f"05 code != 6005: {envelope(step)}")
    require(
        envelope(step).get("msg") == EMPTY_SUB_AGENTS_MSG,
        f"05 msg: {envelope(step).get('msg')!r}",
    )

    step = steps["09_put_with_sub_agents"]
    require(step["status"] == 200, f"09 status {step['status']} != 200")
    entries = payload(step).get("sub_agents") or []
    require(len(entries) == 1, f"09 sub_agents len: {len(entries)}")
    if entries:
        entry = entries[0]
        require(entry.get("name") == "小助手", f"09 name: {entry.get('name')!r}")
        require(
            entry.get("release_policy") == "current",
            f"09 policy: {entry.get('release_policy')!r}",
        )
        require(entry.get("release_id") is None, f"09 release_id: {entry.get('release_id')!r}")

    step = steps["10_db_sub_agents_after_put"]
    stored = step["data"][0] if step["data"] else None
    require(isinstance(stored, list) and len(stored) == 1, f"10 stored: {stored!r}")
    if isinstance(stored, list) and stored:
        require(
            stored[0].get("agent_id") == "<sub_app_id>",
            f"10 agent_id: {stored[0].get('agent_id')!r}",
        )
        require(
            stored[0].get("release_id") is None,
            f"10 release_id: {stored[0].get('release_id')!r}",
        )

    step = steps["11_routing_keyword_match"]
    require(step["status"] == 200, f"11 status {step['status']} != 200")
    require(
        payload(step).get("initial_agent_id") == "<sub_app_id>",
        f"11 initial: {payload(step).get('initial_agent_id')!r}",
    )
    require(
        same(payload(step).get("total_agents"), 1),
        f"11 total_agents: {payload(step).get('total_agents')!r}",
    )
    require(
        payload(step).get("available_handoff_tools") == [],
        f"11 handoff tools: {payload(step).get('available_handoff_tools')!r}",
    )

    step = steps["12_put_without_sub_agents_keeps_stored"]
    require(step["status"] == 200, f"12 status {step['status']} != 200")
    require(same(envelope(step).get("code"), 0), f"12 code: {envelope(step).get('code')}")

    step = steps["13_db_sub_agents_after_keep"]
    stored = step["data"][0] if step["data"] else None
    require(isinstance(stored, list) and len(stored) == 1, f"13 stored: {stored!r}")
    if isinstance(stored, list) and stored:
        require(stored[0].get("name") == "小助手", f"13 name: {stored[0].get('name')!r}")

    step = steps["14_get_multi_agent_after_keep"]
    require(step["status"] == 200, f"14 status {step['status']} != 200")
    entries = payload(step).get("sub_agents") or []
    require(len(entries) == 1, f"14 sub_agents len: {len(entries)}")
    if entries:
        require(entries[0].get("name") == "小助手", f"14 name: {entries[0].get('name')!r}")
        require(
            entries[0].get("current_release_id") == "<sub_release_id>",
            f"14 current_release_id: {entries[0].get('current_release_id')!r}",
        )

    step = steps["16_routing_foreign_app"]
    require(step["status"] == 403, f"16 status {step['status']} != 403")
    require(same(envelope(step).get("code"), 3003), f"16 code != 3003: {envelope(step)}")
    require(envelope(step).get("msg") == FOREIGN_APP_MSG, f"16 msg: {envelope(step).get('msg')!r}")

    step = steps["18_tools_statistics"]
    require(step["status"] == 200, f"18 status {step['status']} != 200")
    stats = payload(step)
    require(same(stats.get("total_tools"), 3), f"18 total_tools: {stats.get('total_tools')!r}")
    require(
        same(stats.get("enabled_count"), 2),
        f"18 enabled_count: {stats.get('enabled_count')!r}",
    )
    require(
        same(stats.get("disabled_count"), 1),
        f"18 disabled_count: {stats.get('disabled_count')!r}",
    )
    require(
        same(stats.get("type_counts"), {"custom": 3}),
        f"18 type_counts: {stats.get('type_counts')!r}",
    )
    status_counts = stats.get("status_counts") or []
    normalized_counts = sorted(
        (item.get("status"), item.get("count"))
        for item in status_counts
        if isinstance(item, dict)
    )
    require(
        normalized_counts == [("available", "2"), ("unconfigured", "1")],
        f"18 status_counts: {status_counts!r}",
    )

    step = steps["19_db_enabled_count"]
    require(same(step["data"], [2]), f"19 db enabled count: {step['data']!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--monolith", default=os.environ.get("P1_DEFECT_MONOLITH", DEFAULT_MONOLITH)
    )
    parser.add_argument(
        "--app-service", default=os.environ.get("P1_DEFECT_APP_SERVICE", DEFAULT_APP_SERVICE)
    )
    args = parser.parse_args()

    problems: list[str] = []
    for path in (args.monolith, args.app_service):
        file = Path(path)
        if not file.exists():
            print(f"[defect] recording {path} not found", file=sys.stderr)
            return 2
        check(json.loads(file.read_text(encoding="utf-8")), problems)

    if problems:
        print(f"[defect] {len(problems)} expectation failure(s):")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("[defect] all absolute expectations hold on both sides")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
