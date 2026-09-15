#!/usr/bin/env python3
"""Inspect or explicitly adopt an existing Knowledge schema; never repair drift."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Executable both from the checkout and from the service image's scripts directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from migrations.runner import MigrationSafetyError, main  # noqa: E402
from migrations.schema import BASELINE_REVISION, MEDIA_REVISION  # noqa: E402


def cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inspect", "adopt"))
    parser.add_argument(
        "--revision",
        required=True,
        choices=("baseline", "media"),
        help="Choose the exact physical schema to verify; never guesses head",
    )
    args = parser.parse_args()
    revision = BASELINE_REVISION if args.revision == "baseline" else MEDIA_REVISION
    try:
        result = main(args.action, revision)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "error_type": type(exc).__name__,
                    "detail": str(exc)
                    if isinstance(exc, MigrationSafetyError)
                    else "Migration failed; credentials are not printed",
                }
            )
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0 if result.get("matches", True) else 2


if __name__ == "__main__":
    raise SystemExit(cli())
