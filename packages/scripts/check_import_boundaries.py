#!/usr/bin/env python3
"""Validate import and initialization boundaries for root public packages."""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


FORBIDDEN_ROOTS = {"api", "app", "mem_knowledge", "fastapi", "celery"}
PACKAGE_FORBIDDEN_ROOTS = {
    "redbear-model": {"sqlalchemy", "mem_storage"},
    "storage-core": {"redbear_model"},
}
REDBEAR_BASE_MODULES = {
    "__init__.py",
    "contracts.py",
    "errors.py",
    "ports.py",
    "resolver.py",
    "telemetry.py",
}
IMPORT_TIME_FACTORIES = {
    "create_engine",
    "create_async_engine",
    "boto3.client",
    "httpx.Client",
    "httpx.AsyncClient",
    "redis.Redis",
    "Elasticsearch",
}


@dataclass(frozen=True)
class Violation:
    path: Path
    line: int
    reason: str

    def render(self, root: Path) -> str:
        try:
            display_path = self.path.relative_to(root)
        except ValueError:
            display_path = self.path
        return f"{display_path}:{self.line}: {self.reason}"


def _imported_modules(node: ast.AST) -> Iterable[tuple[str, int]]:
    if isinstance(node, ast.Import):
        for alias in node.names:
            yield alias.name, node.lineno
    elif isinstance(node, ast.ImportFrom) and node.module:
        yield node.module, node.lineno


def _call_name(node: ast.Call) -> str | None:
    current: ast.AST = node.func
    parts: list[str] = []
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        return ".".join(reversed(parts))
    return None


def _is_pathless_dotenv_call(node: ast.Call) -> bool:
    name = _call_name(node)
    if name not in {"load_dotenv", "dotenv.load_dotenv"}:
        return False
    if node.args:
        return False
    return not any(keyword.arg == "dotenv_path" for keyword in node.keywords)


def _top_level_calls(tree: ast.Module) -> Iterable[ast.Call]:
    for statement in tree.body:
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
            yield statement.value
        elif isinstance(statement, (ast.Assign, ast.AnnAssign)):
            value = statement.value
            if isinstance(value, ast.Call):
                yield value


def _scan_file(path: Path, package_name: str) -> list[Violation]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as exc:
        line = getattr(exc, "lineno", 1) or 1
        return [Violation(path, line, f"cannot parse source: {exc}")]

    violations: list[Violation] = []
    package_forbidden = PACKAGE_FORBIDDEN_ROOTS.get(package_name, set())
    for node in ast.walk(tree):
        for module, line in _imported_modules(node):
            root = module.split(".", 1)[0]
            if root in FORBIDDEN_ROOTS:
                violations.append(
                    Violation(path, line, f"forbidden import {module}")
                )
            elif root in package_forbidden:
                violations.append(
                    Violation(
                        path,
                        line,
                        f"{package_name} cannot import {root}",
                    )
                )
            if (
                package_name == "redbear-model"
                and path.name in REDBEAR_BASE_MODULES
                and module.startswith(("redbear_model.runtime", "redbear_model.providers"))
            ):
                violations.append(
                    Violation(
                        path,
                        line,
                        "redbear-model base modules cannot import runtime or providers",
                    )
                )
        if isinstance(node, ast.Call) and _is_pathless_dotenv_call(node):
            violations.append(
                Violation(path, node.lineno, "pathless load_dotenv() is forbidden")
            )

    for call in _top_level_calls(tree):
        name = _call_name(call)
        if name in IMPORT_TIME_FACTORIES:
            violations.append(
                Violation(
                    path,
                    call.lineno,
                    f"import-time external client initialization is forbidden: {name}",
                )
            )
    return violations


def scan(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    for package_name, import_name in (
        ("redbear-model", "redbear_model"),
        ("storage-core", "mem_storage"),
    ):
        source_root = root / "packages" / package_name / "src" / import_name
        if not source_root.exists():
            continue
        for path in sorted(source_root.rglob("*.py")):
            violations.extend(_scan_file(path, package_name))
    return violations


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Repository root containing packages/",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    violations = scan(root)
    if violations:
        for violation in violations:
            print(violation.render(root))
        return 1
    print("public package import boundaries passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
