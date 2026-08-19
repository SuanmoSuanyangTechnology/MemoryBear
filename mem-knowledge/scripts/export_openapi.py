"""Export the A2 internal OpenAPI contract to stdout."""

from __future__ import annotations

import json
import sys

from mem_knowledge.main import app

EXPECTED_PATHS = {
    "/internal/v1/health/live",
    "/internal/v1/health/ready",
}


def main() -> None:
    schema = app.openapi()
    paths = set(schema.get("paths", {}))
    if paths != EXPECTED_PATHS:
        raise RuntimeError(f"Unexpected A2 OpenAPI paths: {sorted(paths)}")
    json.dump(schema, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
