"""Compare legacy knowledge routes with the internal Knowledge OpenAPI."""

from __future__ import annotations

import argparse
import ast
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, order=True)
class Operation:
    """One HTTP operation with its source handler."""

    method: str
    path: str
    handler: str


@dataclass(frozen=True)
class ControllerSpec:
    """Legacy controller file and its mounted path prefix."""

    filename: str
    prefix: str


CONTROLLERS = (
    ControllerSpec("knowledge_controller.py", "/knowledges"),
    ControllerSpec("knowledge_metadata_controller.py", "/knowledges"),
    ControllerSpec("file_controller.py", "/files"),
    ControllerSpec("document_controller.py", "/documents"),
    ControllerSpec("chunk_controller.py", "/chunks"),
    ControllerSpec("knowledgeshare_controller.py", "/knowledgeshares"),
)

EXPECTED_COUNTS = {
    "chunk_controller.py": 11,
    "document_controller.py": 10,
    "file_controller.py": 8,
    "knowledge_controller.py": 18,
    "knowledge_metadata_controller.py": 7,
    "knowledgeshare_controller.py": 4,
}

HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


def normalize_internal_operation(method: str, legacy_path: str) -> tuple[str, str]:
    """Map a legacy controller operation to its internal service path."""

    return method.upper(), f"/internal/v1{legacy_path}"


def _route_from_decorator(decorator: ast.expr) -> tuple[str, str] | None:
    if not isinstance(decorator, ast.Call):
        return None
    target = decorator.func
    if not isinstance(target, ast.Attribute):
        return None
    if not isinstance(target.value, ast.Name) or target.value.id != "router":
        return None
    if target.attr not in HTTP_METHODS or not decorator.args:
        return None
    path = decorator.args[0]
    if not isinstance(path, ast.Constant) or not isinstance(path.value, str):
        raise ValueError("Knowledge route paths must be string literals")
    return target.attr, path.value


def _controller_operations(path: Path, prefix: str) -> list[Operation]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    operations: list[Operation] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            route = _route_from_decorator(decorator)
            if route is None:
                continue
            method, relative_path = route
            normalized_method, internal_path = normalize_internal_operation(
                method,
                f"{prefix}{relative_path}",
            )
            operations.append(
                Operation(
                    method=normalized_method,
                    path=internal_path,
                    handler=node.name,
                )
            )
    return operations


def collect_legacy_operations(
    legacy_root: Path,
) -> tuple[tuple[Operation, ...], dict[str, int]]:
    """Load and validate the fixed six-controller migration inventory."""

    controller_root = legacy_root / "app" / "controllers"
    operations: list[Operation] = []
    counts: dict[str, int] = {}
    for spec in CONTROLLERS:
        controller_path = controller_root / spec.filename
        if not controller_path.is_file():
            raise FileNotFoundError(f"Legacy controller is missing: {controller_path}")
        controller_operations = _controller_operations(controller_path, spec.prefix)
        counts[spec.filename] = len(controller_operations)
        operations.extend(controller_operations)

    if counts != EXPECTED_COUNTS:
        raise ValueError(
            f"Legacy controller counts changed: expected={EXPECTED_COUNTS} actual={counts}"
        )
    if len(operations) != 58:
        raise ValueError(f"Expected 58 legacy operations, found {len(operations)}")

    operation_keys = {(item.method, item.path) for item in operations}
    if len(operation_keys) != len(operations):
        raise ValueError("Legacy knowledge controllers contain duplicate operations")
    return tuple(sorted(operations)), dict(sorted(counts.items()))


def compare_openapi_operations(
    schema: Mapping[str, Any],
    expected: Sequence[Operation],
) -> list[str]:
    """Return deterministic method/path parity errors."""

    expected_keys = {(item.method, item.path) for item in expected}
    actual_keys: set[tuple[str, str]] = set()
    for path, path_item in schema.get("paths", {}).items():
        if path.startswith("/internal/v1/health/"):
            continue
        if not isinstance(path_item, Mapping):
            continue
        for method in HTTP_METHODS:
            if method in path_item:
                actual_keys.add((method.upper(), path))

    errors: list[str] = []
    for method, path in sorted(expected_keys - actual_keys):
        errors.append(f"missing internal operation: {method} {path}")
    for method, path in sorted(actual_keys - expected_keys):
        errors.append(f"unexpected internal operation: {method} {path}")
    return errors


def _inventory_payload(
    operations: Sequence[Operation],
    counts: Mapping[str, int],
) -> dict[str, Any]:
    return {
        "counts": dict(counts),
        "operation_count": len(operations),
        "operations": [asdict(item) for item in operations],
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy-root", required=True, type=Path)
    parser.add_argument("--inventory-only", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        operations, counts = collect_legacy_operations(args.legacy_root)
    except (FileNotFoundError, SyntaxError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    payload = _inventory_payload(operations, counts)
    if args.json:
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    else:
        print(f"legacy knowledge operations: {len(operations)}")
        for filename, count in counts.items():
            print(f"{filename}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
