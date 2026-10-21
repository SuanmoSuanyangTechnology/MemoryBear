#!/usr/bin/env python
"""Parity battery, comparison step: deep-diff the two recordings written by
``parity_monolith.py`` and ``parity_app_service.py``.

Steps are matched by name; each step compares the HTTP status and the
normalized body (or database row). Run with any Python 3.12 interpreter —
stdlib only.

Run (from the enterprise repo root):
  uv run python core/application-service/scripts/p1_verify/parity_compare.py

Exit code 0 when every step matches; 1 when any step diverges.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

DEFAULT_MONOLITH = "/tmp/app_split_p1_parity_monolith.json"
DEFAULT_APP_SERVICE = "/tmp/app_split_p1_parity_app_service.json"
MAX_DIFFS_PER_STEP = 8


def load_recording(path: str) -> dict:
    file = Path(path)
    if not file.exists():
        print(f"[parity] recording {path} not found; run the runner first", file=sys.stderr)
        raise SystemExit(2)
    return json.loads(file.read_text(encoding="utf-8"))


def diff_values(left, right, path: str, diffs: list[str], limit: int) -> None:
    if len(diffs) >= limit:
        return
    if isinstance(left, dict) and isinstance(right, dict):
        for key in sorted(set(left) | set(right)):
            if key not in left:
                diffs.append(f"{path}.{key}: only right = {right[key]!r}")
            elif key not in right:
                diffs.append(f"{path}.{key}: only left = {left[key]!r}")
            else:
                diff_values(left[key], right[key], f"{path}.{key}", diffs, limit)
            if len(diffs) >= limit:
                return
        return
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            diffs.append(f"{path}: list length left={len(left)} right={len(right)}")
        for index, (lv, rv) in enumerate(zip(left, right, strict=False)):
            diff_values(lv, rv, f"{path}[{index}]", diffs, limit)
            if len(diffs) >= limit:
                return
        return
    if type(left) is not type(right) and not (
        isinstance(left, (int, float))
        and not isinstance(left, bool)
        and isinstance(right, (int, float))
        and not isinstance(right, bool)
    ):
        diffs.append(f"{path}: type left={type(left).__name__} right={type(right).__name__}")
        return
    if left != right:
        diffs.append(f"{path}: left={left!r} right={right!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--monolith",
        default=os.environ.get("P1_PARITY_MONOLITH", DEFAULT_MONOLITH),
    )
    parser.add_argument(
        "--app-service",
        default=os.environ.get("P1_PARITY_APP_SERVICE", DEFAULT_APP_SERVICE),
    )
    args = parser.parse_args()

    monolith = load_recording(args.monolith)
    app_service = load_recording(args.app_service)
    left_steps = {step["step"]: step for step in monolith["steps"]}
    right_steps = {step["step"]: step for step in app_service["steps"]}

    print(
        f"[parity] monolith db={monolith.get('database')} "
        f"steps={len(left_steps)} | app-service db={app_service.get('database')} "
        f"steps={len(right_steps)}"
    )

    diverged = 0
    only_left = sorted(set(left_steps) - set(right_steps))
    only_right = sorted(set(right_steps) - set(left_steps))
    for name in only_left:
        print(f"[PARITY] {name}: missing on application-service side")
        diverged += 1
    for name in only_right:
        print(f"[PARITY] {name}: missing on monolith side")
        diverged += 1

    matched = 0
    for name in sorted(set(left_steps) & set(right_steps)):
        left = left_steps[name]
        right = right_steps[name]
        diffs: list[str] = []
        if left["status"] != right["status"]:
            diffs.append(f"status: left={left['status']} right={right['status']}")
        diff_values(left["data"], right["data"], "data", diffs, MAX_DIFFS_PER_STEP)
        if diffs:
            diverged += 1
            print(f"[DIFF]   {name}")
            for entry in diffs[:MAX_DIFFS_PER_STEP]:
                print(f"         {entry}")
            if len(diffs) >= MAX_DIFFS_PER_STEP:
                print("         ... (truncated)")
        else:
            matched += 1
            print(f"[OK]     {name}")

    total = len(set(left_steps) | set(right_steps))
    print(f"[parity] matched {matched}/{total} steps, {diverged} diverged")
    return 1 if diverged else 0


if __name__ == "__main__":
    raise SystemExit(main())
