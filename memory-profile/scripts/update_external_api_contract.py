"""Rewrite the external API contract snapshot from the current routers.

Run this only after accepting a change to an API-key reachable endpoint's
request parameters: the resulting diff is the contract change reviewers see.

    uv run python scripts/update_external_api_contract.py
"""

from __future__ import annotations

import sys
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    sys.path.insert(0, str(SERVICE_ROOT))

    from src.common.external_api import (  # noqa: E402
        SNAPSHOT_PATH,
        build_external_api_contract,
        dump_external_api_contract,
    )
    from src.modules import router  # noqa: E402

    contract = build_external_api_contract(router.routes)
    SNAPSHOT_PATH.write_text(dump_external_api_contract(contract), encoding="utf-8")
    print(f"wrote {SNAPSHOT_PATH} ({len(contract['routes'])} external API routes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
