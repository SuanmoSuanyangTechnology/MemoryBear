#!/usr/bin/env python
"""Static guard: async routes must not depend on a sync DB session.

FastAPI runs ``async def`` endpoints on the event loop, but a sync ``Session``
(``db: DbSession`` / ``Depends(get_sync_db)``) checks out a QueuePool connection
with a blocking ``pool:get``; once the pool is empty the event loop stalls for
up to ``pool_timeout`` and health probes start failing. Streaming responses
make it worse: FastAPI tears sync yield dependencies down only after the
response body completes, so every SSE stream pins a connection for its whole
lifetime.

The scan flags every ``async def`` under ``src/api/routes`` whose parameters
carry a sync ``Session`` annotation, resolved through module aliases such as
``DbSession = Annotated[Session, Depends(get_sync_db)]``. Pre-existing
violations are pinned in ``ALLOWED``: the guard fails on new violations and on
stale entries, so fixing one requires shrinking the list.

Read-only; exit code 0 = pass, 1 = hits (prints file:line and handler).
Run from the service directory: ``python scripts/check_async_routes_sync_db.py``
(scans ``src/api/routes`` by default; ``--root`` overrides).
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

# Known pre-existing violations, ``relative_path:function`` -> occurrences.
# These handlers still take a sync session on the event loop; converting them
# needs async twins of their sync-db services. Remove entries as they are
# fixed (a stale entry fails the guard on purpose).
ALLOWED: dict[str, int] = {
    "public_share.py:config_query": 1,
    "public_share.py:delete_message": 1,
    "public_share.py:get_user_feedback": 1,
    "public_share.py:list_conversation_shares": 1,
    "public_share.py:report_message": 1,
    "public_share.py:revoke_share": 1,
    "public_share.py:share_conversation": 1,
    "public_share.py:submit_message_feedback": 1,
    "public_share.py:submit_shared_human_intervention": 1,
    "public_share.py:switch_message_version": 1,
    "public_share.py:toggle_message_favorite": 1,
}

MAX_RESOLVE_DEPTH = 10


def _iter_python_files(root: Path):
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _module_aliases(tree: ast.Module) -> dict[str, ast.expr]:
    aliases: dict[str, ast.expr] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    aliases.setdefault(target.id, node.value)
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        ):
            aliases.setdefault(node.target.id, node.value)
    return aliases


def _is_sync_session(
    expr: ast.expr | None,
    aliases: dict[str, ast.expr],
    depth: int = 0,
) -> bool:
    """Whether an annotation expression resolves to a sync SQLAlchemy Session."""
    if expr is None or depth > MAX_RESOLVE_DEPTH:
        return False
    if isinstance(expr, ast.Name):
        if expr.id == "Session":
            return True
        if expr.id == "AsyncSession":
            return False
        if expr.id in aliases:
            return _is_sync_session(aliases[expr.id], aliases, depth + 1)
        return False
    if isinstance(expr, ast.Attribute):
        if expr.attr == "Session":
            return True
        if expr.attr == "AsyncSession":
            return False
        return _is_sync_session(expr.value, aliases, depth + 1)
    if isinstance(expr, ast.Subscript):
        if not isinstance(expr.value, ast.Name):
            return False
        if expr.value.id in {"Annotated", "Optional"}:
            elts = expr.slice.elts if isinstance(expr.slice, ast.Tuple) else [expr.slice]
            if expr.value.id == "Annotated" and not elts:
                return False
            return _is_sync_session(elts[0], aliases, depth + 1)
        if expr.value.id == "Union":
            elts = expr.slice.elts if isinstance(expr.slice, ast.Tuple) else [expr.slice]
            return any(_is_sync_session(elt, aliases, depth + 1) for elt in elts)
        return False
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.BitOr):
        return _is_sync_session(expr.left, aliases, depth + 1) or _is_sync_session(
            expr.right, aliases, depth + 1
        )
    return False


def _param_annotations(node: ast.AsyncFunctionDef):
    args = (
        list(node.args.posonlyargs)
        + list(node.args.args)
        + list(node.args.kwonlyargs)
    )
    if node.args.vararg is not None:
        args.append(node.args.vararg)
    if node.args.kwarg is not None:
        args.append(node.args.kwarg)
    return [(arg.arg, arg.annotation) for arg in args if arg.annotation is not None]


def _scan_tree(tree: ast.AST) -> list[tuple[int, str, str]]:
    """(lineno, function, parameter) for async defs taking a sync Session."""
    aliases = _module_aliases(tree) if isinstance(tree, ast.Module) else {}
    hits: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        for name, annotation in _param_annotations(node):
            if _is_sync_session(annotation, aliases):
                hits.append((node.lineno, node.name, name))
                break
    return hits


def scan_source(source: str, filename: str = "<memory>") -> list[tuple[int, str]]:
    tree = ast.parse(source, filename=filename)
    return [
        (lineno, f"{func} (parameter {param})")
        for lineno, func, param in _scan_tree(tree)
    ]


def scan_root(root: Path) -> dict[str, list[int]]:
    found: dict[str, list[int]] = {}
    for path in _iter_python_files(root):
        relative = path.relative_to(root)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            found.setdefault(f"{relative}:<unparsable>", []).append(0)
            continue
        for lineno, func, _param in _scan_tree(tree):
            found.setdefault(f"{relative}:{func}", []).append(lineno)
    return found


def main() -> int:
    parser = argparse.ArgumentParser(
        description="application-service async-route sync-session static guard"
    )
    parser.add_argument("--root", default="src/api/routes", help="scan root")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"[check] scan root does not exist: {root}", file=sys.stderr)
        return 2

    actual = scan_root(root)
    unexpected = {
        key: lines
        for key, lines in actual.items()
        if len(lines) > ALLOWED.get(key, 0)
    }
    stale = {
        key: count
        for key, count in ALLOWED.items()
        if len(actual.get(key, [])) < count
    }

    scanned = len(_iter_python_files(root))
    if unexpected or stale:
        if unexpected:
            print(
                f"[check] {len(unexpected)} async handler(s) take a sync DB session "
                "without an allowlist entry (run on an async session instead):"
            )
            for key, lines in sorted(unexpected.items()):
                print(f"  - {key}: line(s) {lines}")
        if stale:
            print(
                "[check] stale allowlist entries (handler fixed or removed; "
                "shrink ALLOWED):"
            )
            for key, count in sorted(stale.items()):
                print(f"  - {key} (allowed {count}, found {len(actual.get(key, []))})")
        return 1

    print(
        f"[check] OK: {scanned} route files, no async handler outside the "
        f"{sum(ALLOWED.values())} pinned sync-session violations"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
