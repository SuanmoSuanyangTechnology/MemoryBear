#!/usr/bin/env python3
"""Run ruff on a PR's changed Python files and report only findings on changed lines.

The repository has pre-existing lint findings, so a plain repo-wide ruff gate
would fail every PR. This script narrows reporting to lines the PR actually
added or modified, which keeps the gate actionable without a cleanup campaign
first.

Usage:
    ruff_changed.py <base_ref> [--select RULES] [--path-prefix PREFIX]

Emits GitHub Actions error annotations and exits 1 when findings remain.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from typing import Dict, List, Set

# Hunk header: @@ -old,+new @@ — only the new-side range matters here.
HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def run(cmd: List[str]) -> str:
    """Run a command and return stdout, raising on failure.

    Args:
        cmd: Command argv.

    Returns:
        Captured stdout.

    Raises:
        SystemExit: If the command fails.
    """
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"::error::command failed: {' '.join(cmd)}\n{proc.stderr}", file=sys.stderr)
        raise SystemExit(1)
    return proc.stdout


def changed_files(base: str, prefix: str) -> List[str]:
    """List Python files changed versus the merge base.

    Args:
        base: Base commit-ish to diff against.
        prefix: Only return paths starting with this prefix.

    Returns:
        Paths of added/copied/modified/renamed Python files.
    """
    out = run(["git", "diff", "--name-only", "--diff-filter=ACMR", f"{base}...HEAD"])
    return [
        line
        for line in out.splitlines()
        if line.endswith(".py") and line.startswith(prefix)
    ]


def changed_lines(base: str, path: str) -> Set[int]:
    """Collect new-side line numbers touched by the diff for one file.

    Args:
        base: Base commit-ish to diff against.
        path: File path to inspect.

    Returns:
        Set of 1-based line numbers that the diff added or modified.
    """
    out = run(["git", "diff", "--unified=0", f"{base}...HEAD", "--", path])
    lines: Set[int] = set()
    cursor = 0
    for line in out.splitlines():
        match = HUNK_RE.match(line)
        if match:
            cursor = int(match.group(1))
            continue
        if line.startswith("+") and not line.startswith("+++"):
            lines.add(cursor)
            cursor += 1
    return lines


def main() -> int:
    """Entry point.

    Returns:
        Process exit code: 1 when findings are reported, else 0.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("base")
    parser.add_argument("--select", default="F811,F821,F402")
    parser.add_argument("--path-prefix", default="api/")
    args = parser.parse_args()

    files = changed_files(args.base, args.path_prefix)
    if not files:
        print("No changed Python files under the configured prefix; nothing to lint.")
        return 0

    print(f"Linting {len(files)} changed Python file(s) with rules: {args.select}")

    proc = subprocess.run(
        [
            "ruff",
            "check",
            "--isolated",
            "--no-cache",
            "--select",
            args.select,
            "--output-format",
            "json",
            *files,
        ],
        capture_output=True,
        text=True,
    )
    # ruff exits 1 when it reports findings; only a higher code is a real failure.
    if proc.returncode not in (0, 1):
        print(f"::error::ruff failed to run\n{proc.stderr}", file=sys.stderr)
        return 1

    try:
        findings = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError as exc:
        print(f"::error::could not parse ruff output: {exc}", file=sys.stderr)
        return 1

    touched: Dict[str, Set[int]] = {path: changed_lines(args.base, path) for path in files}

    reported = 0
    for item in findings:
        path = item.get("filename", "")
        # ruff prints absolute paths; match them back to repo-relative paths.
        rel = next((f for f in files if path.endswith(f)), path)
        line = (item.get("location") or {}).get("row")
        if line is None or line not in touched.get(rel, set()):
            continue
        code = item.get("code") or "ruff"
        message = (item.get("message") or "").replace("\n", " ")
        print(f"::error file={rel},line={line},title=ruff {code}::{code}: {message}")
        reported += 1

    total = len(findings)
    skipped = total - reported
    print(
        f"\nruff findings on changed lines: {reported} "
        f"(ignored {skipped} pre-existing finding(s) outside the diff)"
    )
    return 1 if reported else 0


if __name__ == "__main__":
    raise SystemExit(main())
