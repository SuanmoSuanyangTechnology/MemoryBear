"""Fail when memory-jobs imports an async driver or the API package.

This service exists to run Celery tasks on blocking clients. An async client
would reintroduce the event-loop/fork problems this service avoids, and an
import from ``api``/``app`` would couple two separately deployed services.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = SERVICE_ROOT / "src"

FORBIDDEN_IMPORT_ROOTS = {"api", "app"}
FORBIDDEN_MODULE_PREFIXES = (
    "sqlalchemy.ext.asyncio",
    "redis.asyncio",
    "elasticsearch._async",
    "elastic_transport._async",
)
FORBIDDEN_NAMES = {
    "neo4j": {"AsyncDriver", "AsyncGraphDatabase", "AsyncSession"},
    "elasticsearch": {"AsyncElasticsearch"},
    "redis": {"asyncio"},
}


def check(path: Path) -> list[str]:
    failures: list[str] = []
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                failures.extend(_check_module(path, node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                failures.append(f"{path}:{node.lineno}: relative imports are forbidden")
                continue
            if node.module:
                failures.extend(_check_module(path, node.lineno, node.module))
                forbidden = FORBIDDEN_NAMES.get(node.module, set())
                for alias in node.names:
                    if alias.name in forbidden:
                        failures.append(
                            f"{path}:{node.lineno}: async import "
                            f"{node.module}.{alias.name} is forbidden"
                        )
    return failures


def _check_module(path: Path, lineno: int, name: str) -> list[str]:
    failures: list[str] = []
    root = name.split(".", 1)[0]
    if root in FORBIDDEN_IMPORT_ROOTS:
        failures.append(f"{path}:{lineno}: forbidden import {name}")
    for prefix in FORBIDDEN_MODULE_PREFIXES:
        if name == prefix or name.startswith(f"{prefix}."):
            failures.append(f"{path}:{lineno}: async import {name} is forbidden")
    return failures


def main() -> int:
    failures: list[str] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        failures.extend(check(path))
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
