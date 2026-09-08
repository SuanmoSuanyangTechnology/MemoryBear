#!/usr/bin/env python3
"""Export OpenAPI schema from the FastAPI app without running the server.

Mocks all external connections (ES, Neo4j, Redis) so it can run in CI
without any infrastructure.

Experimental isolated mode uses requirements-openapi.txt and rejects heavy
runtime imports. Run from api/ with a dedicated Python 3.12 virtual environment:
    uv pip install --python <venv>/bin/python -r requirements-openapi.txt
    <venv>/bin/python scripts/export_openapi.py --isolated --v1-only

Usage:
    uv run python scripts/export_openapi.py
    uv run python scripts/export_openapi.py --output openapi-current.json
    uv run python scripts/export_openapi.py --v1-only
"""
import argparse
from contextlib import nullcontext
from importlib import import_module
import json
import os
import pathlib
import sys
from unittest.mock import patch, MagicMock
from openapi_export_stubs import isolate_runtime

# Ensure app is importable
api_dir = str(pathlib.Path(__file__).resolve().parent.parent)
if api_dir not in sys.path:
    sys.path.insert(0, api_dir)

# Disable startup tasks
os.environ.setdefault("DB_AUTO_UPGRADE", "false")
os.environ.setdefault("LOAD_MODEL", "false")
os.environ.setdefault("ELASTICSEARCH_HOST", "127.0.0.1")
os.environ.setdefault("ELASTICSEARCH_PORT", "19999")
os.environ.setdefault("NEO4J_URI", "bolt://localhost:7687")
os.environ.setdefault("NEO4J_USERNAME", "neo4j")
os.environ.setdefault("NEO4J_PASSWORD", "dummy_for_schema_export")
os.environ.setdefault("REDIS_HOST", "127.0.0.1")
os.environ.setdefault("REDIS_PORT", "6379")
os.environ.setdefault("SECRET_KEY", "dummy_for_schema_export")
os.environ.setdefault("DB_HOST", "127.0.0.1")
os.environ.setdefault("DB_PORT", "5432")
os.environ.setdefault("DB_USER", "postgres")
os.environ.setdefault("DB_PASSWORD", "dummy")
os.environ.setdefault("DB_NAME", "dummy")


def main():
    parser = argparse.ArgumentParser(description="Export OpenAPI schema")
    parser.add_argument("--output", "-o", default="openapi-baseline.json")
    parser.add_argument("--v1-only", action="store_true", help="Only include /v1 paths")
    parser.add_argument("--isolated", action="store_true", help="Reject heavy runtime dependencies (experimental)")
    parser.add_argument("--audit-output", type=pathlib.Path, help="Write import and installed-package evidence")
    parser.add_argument("--api-dir", type=pathlib.Path, help="Import another checkout's api directory")
    args = parser.parse_args()
    if args.api_dir:
        if not (args.api_dir / "app" / "main.py").is_file():
            parser.error("--api-dir must contain app/main.py")
        sys.path.insert(0, str(args.api_dir.resolve()))

    # Mock external connections and import-time OCR initialization, not API schemas.
    es_mock = MagicMock()
    es_mock.return_value.info.return_value = {"status": "green"}
    es_mock.return_value.ping.return_value = True

    neo4j_mock = MagicMock()
    redis_mock = MagicMock()

    isolation = isolate_runtime() if args.isolated else nullcontext()
    with isolation as guard, patch("elasticsearch.Elasticsearch", es_mock), \
         patch("neo4j.GraphDatabase.driver", return_value=neo4j_mock), \
         patch("neo4j.AsyncGraphDatabase.driver", return_value=neo4j_mock), \
         patch("redis.Redis", return_value=redis_mock), \
         patch("redis.StrictRedis", return_value=redis_mock):
        ocr_patch = nullcontext() if args.isolated else patch.object(
            import_module("app.core.rag.deepdoc.vision"), "OCR", return_value=MagicMock()
        )
        with ocr_patch:
            from app.main import app
            schema = app.openapi()
            if args.audit_output:
                from importlib.metadata import distributions
                audit = {
                    "isolated": args.isolated,
                    "stubbed_modules": guard.stubbed_modules if guard else [],
                    "imported_modules": sorted(sys.modules),
                    "installed_packages": {
                        d.metadata["Name"]: d.version for d in distributions()
                    },
                }

    if args.v1_only:
        schema["paths"] = {k: v for k, v in schema["paths"].items() if k.startswith("/v1")}

    out = pathlib.Path(args.output)
    out.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.audit_output:
        args.audit_output.write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")

    total = len(schema["paths"])
    print(f"Exported {total} paths to {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
