"""Validate and export the complete internal Knowledge OpenAPI contract."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from scripts.compare_legacy_contracts import (
    collect_legacy_operations,
    compare_openapi_operations,
)
from src.main import create_app

EXPECTED_HEALTH_PATHS = {
    "/internal/v1/health/live",
    "/internal/v1/health/ready",
}


def main() -> None:
    schema = create_app().openapi()
    legacy_root = Path(__file__).resolve().parents[2] / "api"
    operations, _counts = collect_legacy_operations(legacy_root)
    errors = compare_openapi_operations(schema, operations)
    if errors:
        raise RuntimeError("Internal OpenAPI mismatch: " + "; ".join(errors))
    paths = set(schema.get("paths", {}))
    if not EXPECTED_HEALTH_PATHS <= paths:
        raise RuntimeError("Internal health paths are missing")
    json.dump(schema, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
