"""Fail when a layer imports something its layer is not allowed to depend on.

The full DDD layout has four layers plus the ORM mappings. This script is the
executable version of that contract:

* ``domain`` depends on the standard library only;
* ``application`` may use ``domain``, its own ports and stdlib, never
  infrastructure, ORM mappings, the process runtime or the Celery interface;
* ``infrastructure`` implements domain/application ports, and may import only
  ``application.ports`` from the application layer;
* ``interfaces`` (Celery tasks) may use application use cases, domain ids and
  the composition root, never infrastructure adapters or ORM mappings;
* ``models`` is a leaf: nothing under ``src`` except other models.

``src/tasks`` is treated as the interface layer until it moves to
``src/interfaces/tasks`` in the migration, so the rule applies in both layouts.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = SERVICE_ROOT / "src"

DOMAIN = "domain"
APPLICATION = "application"
INFRASTRUCTURE = "infrastructure"
INTERFACES = "interfaces"
MODELS = "models"

LAYERS = {
    "domain": DOMAIN,
    "application": APPLICATION,
    "infrastructure": INFRASTRUCTURE,
    "interfaces": INTERFACES,
    "models": MODELS,
}

#: The domain may import stdlib modules and ``__future__``; nothing else.
DOMAIN_ALLOWED = frozenset(sys.stdlib_module_names) | {"__future__"}

FORBIDDEN_BY_LAYER = {
    APPLICATION: (
        "src.infrastructure",
        "src.interfaces",
        "src.models",
        "src.runtime",
        "src.config",
        "src.bootstrap",
        "src.celery_app",
        "src.entry",
        "src.healthcheck",
        "src.composition",
    ),
    INFRASTRUCTURE: (
        "src.interfaces",
        "src.composition",
        "src.entry",
        "src.celery_app",
        "src.healthcheck",
    ),
    INTERFACES: (
        # Tasks name domain ids/policies and call use cases, but they reach
        # adapters only through ``src.composition``. ORM mappings must never
        # appear here either.
        "src.infrastructure",
        "src.models",
        "src.celery_app",
        "src.entry",
    ),
}

#: Third-party packages that only the infrastructure layer may touch.
APPLICATION_FORBIDDEN_THIRD_PARTY = frozenset(
    {
        "celery",
        "elasticsearch",
        "httpx",
        "jinja2",
        "neo4j",
        "redbear_model",
        "redis",
        "sqlalchemy",
        "task_bridge",
    }
)


def layer_for(path: Path) -> str | None:
    """Return the layer a file belongs to, or ``None`` when unregulated."""

    parts = path.parts
    try:
        index = parts.index("src")
    except ValueError:
        return None
    if index + 1 >= len(parts):
        return None
    return LAYERS.get(parts[index + 1])


def check_source(path: Path, source: str) -> list[str]:
    """Return every layer-rule violation in one file."""

    layer = layer_for(path)
    if layer is None:
        return []

    failures: list[str] = []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [f"{path}: cannot parse source: {exc}"]

    for lineno, name in _imports(tree):
        failures.extend(_check_import(path, lineno, name, layer))
    return failures


def _imports(tree: ast.AST) -> list[tuple[int, str]]:
    """Yield ``(lineno, module)`` pairs, composing ``from src import x`` forms."""

    result: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                result.append((node.lineno, "." * node.level + (node.module or "")))
                continue
            if not node.module:
                continue
            result.append((node.lineno, node.module))
            if node.module == "src":
                result.extend((node.lineno, f"src.{alias.name}") for alias in node.names)
    return result


def _check_import(path: Path, lineno: int, name: str, layer: str) -> list[str]:
    if name.startswith("."):
        return [f"{path}:{lineno}: relative imports are forbidden"]

    if layer == DOMAIN:
        # Intra-domain imports are the domain composing itself; everything else
        # must be a standard-library module.
        if name == "src.domain" or name.startswith("src.domain."):
            return []
        root = name.split(".", 1)[0]
        if root not in DOMAIN_ALLOWED:
            return [f"{path}:{lineno}: domain cannot import {name}"]
        return []

    if layer == MODELS:
        if name.startswith("src.") and not name.startswith("src.models"):
            return [f"{path}:{lineno}: models cannot import {name}"]
        return []

    for root in FORBIDDEN_BY_LAYER.get(layer, ()):
        if name == root or name.startswith(f"{root}."):
            return [f"{path}:{lineno}: {layer} cannot import {name}"]

    if layer == APPLICATION:
        root = name.split(".", 1)[0]
        if root in APPLICATION_FORBIDDEN_THIRD_PARTY:
            return [f"{path}:{lineno}: application cannot import third-party {name}"]

    if layer == INFRASTRUCTURE and name.startswith("src.application"):
        if name != "src.application.ports" and not name.startswith(
            "src.application.ports."
        ):
            return [
                f"{path}:{lineno}: infrastructure may only import application.ports, "
                f"not {name}"
            ]

    return []


def main() -> int:
    failures: list[str] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        failures.extend(check_source(path, path.read_text(encoding="utf-8")))
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print("memory-jobs layer boundaries: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
