#!/usr/bin/env python
"""Static guard: this service must not reach into the host domain (monolith core/api).

Checks executed constructs only (AST; docstrings, comments and dead string literals are ignored):

1. **No host package imports**: ``import app`` / ``from app... import ...`` inside ``src/``
2. **Raw SQL**: literals passed to ``text()`` / ``sa_text()`` are scanned for
   ``INSERT INTO`` / ``MERGE INTO`` / ``UPDATE`` / ``DELETE FROM``. The target must be in
   ``SERVICE_WRITE_TABLES``; dynamically assembled targets fail closed
3. **SQLAlchemy Core DML**: ``insert`` / ``update`` / ``delete`` (including
   ``postgresql.insert``) resolve their ORM class argument to ``__tablename__``; writes to
   any other table fail, and unresolvable targets fail closed
4. **ORM bulk DML**: ``session.query(Model)...update()/delete()`` resolves the query model;
   ``session.add/delete/merge(expr)`` checks the argument when its type can be resolved
   through imports, module constants and simple query assignments

Model class -> table names are harvested statically from ``src/models/*.py`` (class-level
``__tablename__``), so the guard needs no ORM mapper configuration and is independent of
environment variables or database connectivity.

Read-only; exit code 0 = pass, 1 = hits (prints file:line and reason).
Usage: ``uv run --project core/application-service python scripts/check_no_host_writes.py [--root src]``
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_ROOT))
from src.models.base import SERVICE_WRITE_TABLES  # noqa: E402

HOST_PACKAGE = "app"
SA_DML_OPS = frozenset({"insert", "update", "delete"})
DML_RE = re.compile(
    r"\b(?:INSERT\s+INTO|MERGE\s+INTO|UPDATE|DELETE\s+FROM)\s+([A-Za-z_][A-Za-z0-9_]*|\?)",
    re.IGNORECASE,
)
DML_KEYWORD_RE = re.compile(r"\b(?:INSERT\s+INTO|MERGE\s+INTO|UPDATE|DELETE\s+FROM)\b", re.IGNORECASE)
# The token after a DML keyword may be a clause rather than a table name
# (e.g. ``ON CONFLICT DO UPDATE SET`` or the rare ``DELETE FROM (SELECT ...)`` form).
SQL_KEYWORDS = frozenset({"select", "set", "values", "where", "only"})
DYNAMIC = "?"
MAX_RESOLVE_DEPTH = 25


def _iter_python_files(root: Path):
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _harvest_model_tables(models_dir: Path) -> dict[str, str]:
    """Class name -> ``__tablename__`` for every ORM class under ``src/models``."""
    mapping: dict[str, str] = {}
    for path in sorted(models_dir.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            for stmt in node.body:
                if not isinstance(stmt, ast.Assign):
                    continue
                named = any(
                    isinstance(target, ast.Name) and target.id == "__tablename__"
                    for target in stmt.targets
                )
                if named and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str):
                    mapping[node.name] = stmt.value.value
    return mapping


TABLE_MAP = _harvest_model_tables(SERVICE_ROOT / "src" / "models")


def _harvest_string_constants(root: Path) -> dict[str, str]:
    """Module-level string constants, for resolving imported SQL templates by name."""
    seen: dict[str, set[str]] = {}
    for path in _iter_python_files(root):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            if not isinstance(node.value, ast.Constant) or not isinstance(node.value.value, str):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    seen.setdefault(target.id, set()).add(node.value.value)
    return {name: next(iter(values)) for name, values in seen.items() if len(values) == 1}


GLOBAL_STRING_CONSTANTS = _harvest_string_constants(SERVICE_ROOT / "src")


def _func_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _imported_host_modules(tree: ast.AST) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == HOST_PACKAGE or alias.name.startswith(f"{HOST_PACKAGE}."):
                    hits.append((node.lineno, f"import {alias.name}"))
        elif isinstance(node, ast.ImportFrom) and node.module and (
            node.module == HOST_PACKAGE or node.module.startswith(f"{HOST_PACKAGE}.")
        ):
            hits.append((node.lineno, f"from {node.module} import ..."))
    return hits


class _FileIndex:
    """Local names needed to resolve executed write targets in one module."""

    def __init__(self, tree: ast.AST):
        self.class_imports: dict[str, str] = {}
        self.sa_ops: dict[str, str] = {}
        self.sa_modules: set[str] = set()
        self.values: dict[str, ast.expr] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    local = alias.asname or alias.name
                    if alias.name in TABLE_MAP:
                        self.class_imports[local] = alias.name
                    if "sqlalchemy" in node.module and alias.name in SA_DML_OPS:
                        self.sa_ops[local] = alias.name
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if "sqlalchemy" in alias.name:
                        self.sa_modules.add(alias.asname or alias.name.split(".")[0])
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        self.values.setdefault(target.id, node.value)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if node.value is not None:
                    self.values.setdefault(node.target.id, node.value)


def _resolve_model(expr: ast.expr | None, index: _FileIndex, depth: int = 0) -> str | None:
    """Best-effort class name of an ORM expression (imports, constants, query chains)."""
    if expr is None or depth > MAX_RESOLVE_DEPTH:
        return None
    if isinstance(expr, ast.Name):
        if expr.id in index.class_imports:
            return index.class_imports[expr.id]
        if expr.id in TABLE_MAP:
            return expr.id
        if expr.id in index.values:
            return _resolve_model(index.values[expr.id], index, depth + 1)
        return None
    if isinstance(expr, ast.Attribute):
        return _resolve_model(expr.value, index, depth + 1)
    if isinstance(expr, ast.Call):
        name = _func_name(expr.func)
        if name in TABLE_MAP:
            return name
        if name == "query" and expr.args:
            return _resolve_model(expr.args[0], index, depth + 1)
        if isinstance(expr.func, ast.Attribute):
            return _resolve_model(expr.func.value, index, depth + 1)
        return None
    if isinstance(expr, ast.Subscript):
        return _resolve_model(expr.value, index, depth + 1)
    return None


def _literal_sql(expr: ast.expr | None, index: _FileIndex, depth: int = 0) -> str | None:
    """Flatten a string expression; formatted slots become ``?`` placeholders."""
    if expr is None or depth > MAX_RESOLVE_DEPTH:
        return None
    if isinstance(expr, ast.Constant):
        return expr.value if isinstance(expr.value, str) else None
    if isinstance(expr, ast.JoinedStr):
        parts: list[str] = []
        for value in expr.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            else:
                parts.append(DYNAMIC)
        return "".join(parts)
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
        left = _literal_sql(expr.left, index, depth + 1)
        right = _literal_sql(expr.right, index, depth + 1)
        if left is not None and right is not None:
            return left + right
        return None
    if isinstance(expr, ast.Name):
        if expr.id in index.values:
            return _literal_sql(index.values[expr.id], index, depth + 1)
        return GLOBAL_STRING_CONSTANTS.get(expr.id)
    return None


def _raw_sql_hits(tree: ast.AST, index: _FileIndex) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _func_name(node.func) not in {"text", "sa_text"}:
            continue
        if not node.args:
            continue
        sql = _literal_sql(node.args[0], index)
        if sql is None:
            hits.append((node.lineno, "text() SQL argument cannot be verified statically"))
            continue
        matches = list(DML_RE.finditer(sql))
        if DML_KEYWORD_RE.search(sql) and not matches:
            hits.append((node.lineno, "DML keyword without a statically resolvable table"))
            continue
        for match in matches:
            table = match.group(1)
            if table.lower() in SQL_KEYWORDS:
                continue
            if table == DYNAMIC:
                hits.append(
                    (node.lineno, f"{match.group(0).strip()} -> dynamic table name (not verifiable)")
                )
                continue
            if table not in SERVICE_WRITE_TABLES:
                hits.append(
                    (node.lineno, f"{match.group(0).strip()} -> {table} (not an owned table)")
                )
    return hits


def _sa_operation(func: ast.expr, index: _FileIndex) -> str | None:
    if isinstance(func, ast.Name):
        return index.sa_ops.get(func.id)
    if isinstance(func, ast.Attribute) and func.attr in SA_DML_OPS:
        if isinstance(func.value, ast.Name) and func.value.id in index.sa_modules:
            return func.attr
    return None


def _core_dml_hits(tree: ast.AST, index: _FileIndex) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        operation = _sa_operation(node.func, index)
        if operation is None:
            continue
        target = node.args[0]
        table: str | None = None
        if isinstance(target, ast.Call) and _func_name(target.func) == "Table" and target.args:
            first = target.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                table = first.value
        if table is None:
            model = _resolve_model(target, index)
            table = TABLE_MAP.get(model) if model else None
        if table is None:
            hits.append((node.lineno, f"{operation}() target cannot be resolved to a known table"))
        elif table not in SERVICE_WRITE_TABLES:
            hits.append((node.lineno, f"{operation}() -> {table} (not an owned table)"))
    return hits


def _query_model(expr: ast.expr | None, index: _FileIndex, depth: int = 0) -> str | None:
    if expr is None or depth > MAX_RESOLVE_DEPTH:
        return None
    if isinstance(expr, ast.Call):
        if _func_name(expr.func) == "query" and expr.args:
            return _resolve_model(expr.args[0], index, depth + 1)
        if isinstance(expr.func, ast.Attribute):
            return _query_model(expr.func.value, index, depth + 1)
        return None
    if isinstance(expr, ast.Name) and expr.id in index.values:
        return _query_model(index.values[expr.id], index, depth + 1)
    if isinstance(expr, ast.Attribute):
        return _query_model(expr.value, index, depth + 1)
    return None


def _query_bulk_hits(tree: ast.AST, index: _FileIndex) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in {"update", "delete"}:
            continue
        model = _query_model(node.func.value, index)
        table = TABLE_MAP.get(model) if model else None
        if table is not None and table not in SERVICE_WRITE_TABLES:
            hits.append(
                (node.lineno, f"query({model}).{node.func.attr}() -> {table} (not an owned table)")
            )
    return hits


def _session_like(expr: ast.expr) -> bool:
    if isinstance(expr, ast.Name):
        name = expr.id.lower()
        return "db" in name or "session" in name
    if isinstance(expr, ast.Attribute):
        return _session_like(expr.value) or "db" in expr.attr.lower() or "session" in expr.attr.lower()
    return False


def _session_orm_hits(tree: ast.AST, index: _FileIndex) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in {"add", "add_all", "delete", "merge"}:
            continue
        if not _session_like(node.func.value):
            continue
        model = _resolve_model(node.args[0], index)
        table = TABLE_MAP.get(model) if model else None
        if table is not None and table not in SERVICE_WRITE_TABLES:
            hits.append((node.lineno, f"session.{node.func.attr}({model}) -> {table} (not an owned table)"))
    return hits


def scan_source(source: str, filename: str = "<memory>") -> list[tuple[int, str]]:
    tree = ast.parse(source, filename=filename)
    index = _FileIndex(tree)
    return (
        _imported_host_modules(tree)
        + _raw_sql_hits(tree, index)
        + _core_dml_hits(tree, index)
        + _query_bulk_hits(tree, index)
        + _session_orm_hits(tree, index)
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="application-service host-domain static guard")
    parser.add_argument("--root", default="src", help="scan root (default: src)")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"[check] scan root does not exist: {root}", file=sys.stderr)
        return 2

    violations: list[str] = []
    scanned = 0
    for path in _iter_python_files(root):
        scanned += 1
        source = path.read_text(encoding="utf-8")
        for lineno, reason in scan_source(source, str(path)):
            violations.append(f"{path.relative_to(root.parent)}:{lineno}: {reason}")

    if violations:
        print(f"[check] {len(violations)} host-domain dependency/write hits ({scanned} files scanned):")
        for item in violations:
            print(f"  - {item}")
        return 1

    print(
        f"[check] OK: {scanned} files, no host package imports, "
        f"no DML against non-owned tables ({len(SERVICE_WRITE_TABLES)} writable tables)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
