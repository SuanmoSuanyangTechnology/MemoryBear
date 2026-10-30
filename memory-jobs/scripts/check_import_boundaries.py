"""Fail when a layer imports something its layer is not allowed to depend on.

The full DDD layout has four layers plus the ORM mappings. This script is the
executable version of that contract:

* ``domain`` depends on the standard library only;
* ``application`` may use ``domain``, its own ports and stdlib, never
  infrastructure, ORM mappings, the process runtime or the Celery interface;
* ``infrastructure`` implements domain/application ports, and may import only
  ``application.ports`` from the application layer;
* ``interfaces`` (Celery tasks) may use application use cases, domain ids and
  the composition root's per-domain facades, never infrastructure adapters,
  ORM mappings or a container adapter accessor;
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

REGISTRY_FILE = SOURCE_ROOT / "composition" / "registry.py"


def _registry_domain_names() -> frozenset[str]:
    """The container's public surface, read from the domain registry.

    The registry is the single list of task domains. Deriving the allowlist from
    it (by parsing, not importing — this checker must stay source-only) means a
    new domain cannot be forgotten here and silently reported as a violation.
    """

    if not REGISTRY_FILE.exists():
        raise RuntimeError(f"{REGISTRY_FILE}: domain registry not found")
    tree = ast.parse(REGISTRY_FILE.read_text(encoding="utf-8"))

    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        callee = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if callee != "DomainModule":
            continue
        for keyword in node.keywords:
            value = keyword.value
            if (
                keyword.arg == "name"
                and isinstance(value, ast.Constant)
                and isinstance(value.value, str)
            ):
                names.add(value.value)

    if not names:
        raise RuntimeError(f"{REGISTRY_FILE}: no DomainModule(name=...) entries found")
    return frozenset(names)


#: One facade per task domain; an interface may ask for one of these but never
#: for an adapter.
CONTAINER_DOMAINS = _registry_domain_names()

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

    failures.extend(_check_container_access(tree, path, layer))
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


def _check_container_access(tree: ast.AST, path: Path, layer: str) -> list[str]:
    """The interface layer may only take a domain facade from the container.

    ``get_container().profile_cache.generate()`` passes; anything reached
    directly from the container (an adapter, a Unit of Work, a Redis client)
    fails, so a task cannot bypass its use case. The check follows container
    expressions through local aliases (``container = get_container()``) and
    through an aliased import, and it refuses to walk into a facade's private
    members (``container.profile_cache._dependencies``).
    """

    if layer != INTERFACES:
        return []

    callables = _container_callables(tree)
    aliases = _container_aliases(tree, callables)
    allowed = "|".join(sorted(CONTAINER_DOMAINS))
    failures: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        chain = _container_chain(node, callables, aliases)
        if chain is None:
            continue
        first, *rest = chain
        if first not in CONTAINER_DOMAINS:
            failures.append(
                f"{path}:{node.lineno}: interfaces may only use a domain facade "
                f"(get_container().<{allowed}>), not get_container().{'.'.join(chain)}"
            )
            continue
        private = next((attr for attr in rest if attr.startswith("_")), None)
        if private is not None:
            failures.append(
                f"{path}:{node.lineno}: interfaces may not reach into a facade's "
                f"private member {private!r} "
                f"(get_container().{'.'.join(chain)})"
            )
    return failures


def _container_callables(tree: ast.AST) -> frozenset[str]:
    """Names that call the container, including an aliased import of it."""

    names = {"get_container"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module != "src.composition":
            continue
        for alias in node.names:
            if alias.name == "get_container":
                names.add(alias.asname or alias.name)
    return frozenset(names)


def _is_container_call(func: ast.expr, callables: frozenset[str]) -> bool:
    if isinstance(func, ast.Name):
        return func.id in callables
    return isinstance(func, ast.Attribute) and func.attr in callables


def _container_chain(
    expr: ast.expr,
    callables: frozenset[str],
    aliases: dict[str, tuple[str, ...]],
) -> list[str] | None:
    """Attribute names from the container root down to ``expr``, if rooted there."""

    attrs: list[str] = []
    node = expr
    while isinstance(node, ast.Attribute):
        attrs.append(node.attr)
        node = node.value
    attrs.reverse()

    if isinstance(node, ast.Call) and _is_container_call(node.func, callables):
        return attrs
    if isinstance(node, ast.Name) and node.id in aliases:
        return [*aliases[node.id], *attrs]
    return None


def _container_aliases(
    tree: ast.AST,
    callables: frozenset[str],
) -> dict[str, tuple[str, ...]]:
    """Names bound to a container expression, with the chain it was bound at.

    Aliases are resolved in source order so ``b = a`` after
    ``a = get_container()`` is followed too. Scoping is deliberately ignored:
    the check is a conservative CI guard, not a dataflow analysis.
    """

    aliases: dict[str, tuple[str, ...]] = {}
    assignments = sorted(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.Assign, ast.AnnAssign))
        ),
        key=lambda node: (node.lineno, node.col_offset),
    )
    for node in assignments:
        if isinstance(node, ast.Assign):
            targets: list[ast.expr] = node.targets
            value: ast.expr | None = node.value
        else:
            targets = [node.target]
            value = node.value
        if value is None:
            continue
        chain = _container_chain(value, callables, aliases)
        if chain is None:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                aliases[target.id] = tuple(chain)
    return aliases


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
