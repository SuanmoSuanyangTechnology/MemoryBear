#!/usr/bin/env python3
"""Export OpenAPI schema from the FastAPI app without running the server.

Usage:
    uv run python scripts/export_openapi.py
    uv run python scripts/export_openapi.py --output openapi-current.json
    uv run python scripts/export_openapi.py --v1-only
"""
import argparse
import json
import os
import pathlib
import sys
from unittest.mock import patch, MagicMock

# Disable startup tasks
os.environ.setdefault("DB_AUTO_UPGRADE", "false")
os.environ.setdefault("LOAD_MODEL", "false")
os.environ.setdefault("ELASTICSEARCH_HOST", "127.0.0.1")
os.environ.setdefault("ELASTICSEARCH_PORT", "19999")


def main():
    parser = argparse.ArgumentParser(description="Export OpenAPI schema")
    parser.add_argument("--output", "-o", default="openapi-baseline.json")
    parser.add_argument("--v1-only", action="store_true", help="Only include /v1 paths")
    args = parser.parse_args()

    # Ensure app is importable
    api_dir = str(pathlib.Path(__file__).resolve().parent.parent)
    if api_dir not in sys.path:
        sys.path.insert(0, api_dir)

    # Mock ES to avoid connection timeout
    es_mock = MagicMock()
    es_mock.return_value.info.return_value = {"status": "green"}
    es_mock.return_value.ping.return_value = True

    with patch("elasticsearch.Elasticsearch", es_mock):
        from app.main import app
        schema = app.openapi()

    if args.v1_only:
        schema["paths"] = {k: v for k, v in schema["paths"].items() if k.startswith("/v1")}

    out = pathlib.Path(args.output)
    out.write_text(json.dumps(schema, indent=2, ensure_ascii=False))

    total = len(schema["paths"])
    print(f"Exported {total} paths to {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
