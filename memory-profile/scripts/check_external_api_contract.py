"""Check the running routes against the committed external API contract.

Exit code 0 means the snapshot matches; 1 means it is stale (or missing) and
must be regenerated with ``scripts/update_external_api_contract.py`` after the
change is accepted.

    uv run python scripts/check_external_api_contract.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare @external_api routes with the committed parameter snapshot."
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=None,
        help="snapshot to check (default: src/external_api_contract.json)",
    )
    args = parser.parse_args(argv)

    sys.path.insert(0, str(SERVICE_ROOT))

    from src.common.external_api import (  # noqa: E402
        SNAPSHOT_PATH,
        build_external_api_contract,
        diff_external_api_contract,
    )
    from src.modules import router  # noqa: E402

    snapshot_path = args.snapshot or SNAPSHOT_PATH
    try:
        expected = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"missing snapshot: {snapshot_path}")
        return 1
    except (OSError, ValueError) as exc:
        print(f"unreadable snapshot: {snapshot_path}: {exc}")
        return 1

    current = build_external_api_contract(router.routes)
    try:
        issues = diff_external_api_contract(expected, current)
    except ValueError as exc:
        print(f"invalid snapshot: {snapshot_path}: {exc}")
        return 1

    if not issues:
        print(f"OK: {len(current['routes'])} external API routes match {snapshot_path}")
        return 0

    print(f"{len(issues)} external API contract change(s) against {snapshot_path}:")
    for issue in issues:
        print(f"  - {issue}")
    print("Run scripts/update_external_api_contract.py after accepting these changes.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
