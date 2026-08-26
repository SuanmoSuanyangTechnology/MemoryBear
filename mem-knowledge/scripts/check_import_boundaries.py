"""Check ordinary source imports and the declared knowledge task surface."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SERVICE_ROOT = REPOSITORY_ROOT / "mem-knowledge"
SOURCE_ROOT = SERVICE_ROOT / "src"
API_ROOT = (REPOSITORY_ROOT / "api").resolve()
PUBLIC_PACKAGE_ROOTS = (
    REPOSITORY_ROOT / "packages" / "redbear-model" / "src",
    REPOSITORY_ROOT / "packages" / "storage-core" / "src",
)
FORBIDDEN_SERVICE_IMPORTS = {"api", "app", "premium"}
AUTHORIZED_TASKS_BY_MODULE = {
    "tasks/document.py": {
        "app.core.rag.tasks.parse_document",
        "app.core.rag.tasks.sync_knowledge_for_kb",
    },
    "tasks/evidence_graph.py": {
        "app.core.rag.tasks.sync_evidence_graph_document",
        "app.core.rag.tasks.rebuild_evidence_graph_knowledge",
        "app.core.rag.tasks.clear_all_knowledge_graph_data",
    },
    "tasks/legacy_compat.py": {
        "app.core.rag.tasks.build_graphrag_for_kb",
        "app.core.rag.tasks.build_graphrag_for_document",
        "app.core.rag.tasks.migrate_evidence_graph_knowledge",
    },
    "tasks/qa_import.py": {"app.core.rag.tasks.import_qa_chunks"},
}
RUNTIME_ROOT_MODULES = frozenset(
    {
        "main.py",
        "api/router.py",
        "rag/chunk/router.py",
        "tasks/celery_worker.py",
        *AUTHORIZED_TASKS_BY_MODULE,
    }
)
REQUIRED_RUNTIME_ROOT_MODULES = RUNTIME_ROOT_MODULES
# This finite source scan recognizes only declared protocol tombstones and
# documented dynamic entries. It is an engineering guardrail, not a sandbox
# proof against arbitrary Python reflection or runtime imports.
RUNTIME_DYNAMIC_IMPORT_ALLOWLIST: dict[str, str] = {}
REMOVED_RUNTIME_MODULES = frozenset(
    {
        "rag/chunk/parser/markdown.py",
        "rag/knowledge_graph/rebuild_task_guard.py",
    }
)


def _top_level_imports(tree: ast.AST) -> set[str]:
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imports.add(node.module.split(".", 1)[0])
    return imports


def _task_decorators(tree: ast.AST) -> list[tuple[int, str | None]]:
    """Return direct ``*.task`` and ``shared_task`` decorators.

    This is intentionally a source guardrail, not a Python capability sandbox.
    The runtime Celery registry is the authoritative task-registration check.
    """

    decorators: list[tuple[int, str | None]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            is_task = isinstance(target, ast.Attribute) and target.attr == "task"
            is_shared_task = isinstance(target, ast.Name) and target.id == "shared_task"
            if not (is_task or is_shared_task):
                continue
            task_name = None
            if isinstance(decorator, ast.Call):
                for keyword in decorator.keywords:
                    if (
                        keyword.arg == "name"
                        and isinstance(keyword.value, ast.Constant)
                        and isinstance(keyword.value.value, str)
                    ):
                        task_name = keyword.value.value
            decorators.append((decorator.lineno, task_name))
    return decorators


def _display_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(REPOSITORY_ROOT))
    except ValueError:
        return str(path.relative_to(root))


def _python_modules(root: Path) -> dict[tuple[str, ...], str]:
    modules: dict[tuple[str, ...], str] = {}
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        parts = relative.with_suffix("").parts
        module_parts = parts[:-1] if parts[-1] == "__init__" else parts
        modules[module_parts] = relative.as_posix()
    return modules


def _module_imports(
    path: Path,
    module_parts: tuple[str, ...],
    modules: dict[tuple[str, ...], str],
) -> set[str]:
    """Return source-tree modules named by ordinary imports in ``path``."""

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()

    def add_if_present(parts: tuple[str, ...]) -> None:
        module_path = modules.get(parts)
        if module_path:
            imported.add(module_path)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                add_if_present(tuple(alias.name.split(".")))
        elif isinstance(node, ast.ImportFrom):
            package = module_parts if path.name == "__init__.py" else module_parts[:-1]
            if node.level:
                if node.level - 1 > len(package):
                    continue
                base = package[: len(package) - (node.level - 1)]
            else:
                base = ()
            target = base + tuple(node.module.split(".")) if node.module else base
            add_if_present(target)
            for alias in node.names:
                if alias.name != "*":
                    add_if_present(target + (alias.name,))
    return imported


def _reachable_runtime_modules(
    root: Path,
    *,
    runtime_roots: set[str] | frozenset[str],
    dynamic_import_allowlist: dict[str, str] | None = None,
) -> set[str]:
    """Follow finite AST import edges from API, task, and parser roots."""

    modules = _python_modules(root)
    paths_to_modules = {path: module for module, path in modules.items()}
    reachable = {
        path
        for path in runtime_roots | set(dynamic_import_allowlist or {})
        if path in paths_to_modules
    }
    pending = list(reachable)
    while pending:
        current = pending.pop()
        module_parts = paths_to_modules[current]
        for imported in _module_imports(root / current, module_parts, modules):
            if imported not in reachable:
                reachable.add(imported)
                pending.append(imported)
    return reachable


def _removed_runtime_module_errors(
    root: Path,
    *,
    runtime_roots: set[str] | frozenset[str],
    removed_modules: set[str] | frozenset[str],
    dynamic_import_allowlist: dict[str, str] | None = None,
) -> list[str]:
    """Reject a removed compatibility module if a runtime root reaches it."""

    reachable = _reachable_runtime_modules(
        root,
        runtime_roots=runtime_roots,
        dynamic_import_allowlist=dynamic_import_allowlist,
    )
    return [
        f"removed runtime module is reachable: {path}"
        for path in sorted(removed_modules & reachable)
    ]


def _scan_runtime_reachability(
    root: Path,
    *,
    runtime_roots: set[str] | frozenset[str],
    required_roots: set[str] | frozenset[str],
    dynamic_import_allowlist: dict[str, str] | None = None,
) -> list[str]:
    """Report dead modules and duplicate public classes in the finite graph.

    This guard deliberately models only ordinary source imports plus declared
    roots. It is not a Python reflection sandbox or a proof about arbitrary
    dynamic import behavior.
    """

    modules = _python_modules(root)
    known_paths = set(modules.values())
    errors = [
        f"required runtime root is not configured: {path}"
        for path in sorted(required_roots - runtime_roots)
    ]
    for path, reason in sorted((dynamic_import_allowlist or {}).items()):
        if path not in known_paths:
            errors.append(f"dynamic import allowlist module is missing: {path}")
        elif not reason.strip():
            errors.append(f"dynamic import allowlist reason is missing: {path}")

    reachable = _reachable_runtime_modules(
        root,
        runtime_roots=runtime_roots,
        dynamic_import_allowlist=dynamic_import_allowlist,
    )
    errors.extend(
        _removed_runtime_module_errors(
            root,
            runtime_roots=runtime_roots,
            removed_modules=REMOVED_RUNTIME_MODULES,
            dynamic_import_allowlist=dynamic_import_allowlist,
        )
    )
    for module_path in sorted(known_paths - reachable):
        if not module_path.endswith("/__init__.py") and module_path != "__init__.py":
            errors.append(f"unreachable runtime module: {module_path}")

    classes: dict[tuple[str, str], list[str]] = {}
    for module_path in sorted(known_paths):
        tree = ast.parse((root / module_path).read_text(encoding="utf-8"), filename=module_path)
        imported_names = {
            alias.asname or alias.name: f"{node.level}:{node.module}:{alias.name}"
            for node in tree.body
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
                if any(
                    isinstance(decorator, ast.Name) and decorator.id == "dataclass"
                    or isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Name)
                    and decorator.func.id == "dataclass"
                    for decorator in node.decorator_list
                ):
                    continue
                bases = ", ".join(
                    imported_names.get(base.id, f"local:{base.id}")
                    if isinstance(base, ast.Name)
                    else ast.unparse(base)
                    for base in node.bases
                )
                classes.setdefault((node.name, bases), []).append(module_path)
    for (class_name, _bases), module_paths in sorted(classes.items()):
        if len(module_paths) > 1:
            errors.append(
                f"duplicate public class {class_name}: {', '.join(sorted(module_paths))}"
            )
    return errors


def _scan_service_tree(root: Path, *, require_complete_registry: bool = False) -> list[str]:
    """Validate direct imports and the nine literal task declarations."""

    errors: list[str] = []
    discovered: dict[str, set[str]] = {}
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        display_path = _display_path(path, root)
        imports = _top_level_imports(tree) & FORBIDDEN_SERVICE_IMPORTS
        if imports:
            errors.append(f"{display_path} imports forbidden roots: {', '.join(sorted(imports))}")

        module_path = path.relative_to(root).as_posix()
        authorized_names = AUTHORIZED_TASKS_BY_MODULE.get(module_path, set())
        module_discovered = discovered.setdefault(module_path, set())
        for line, task_name in _task_decorators(tree):
            if task_name not in authorized_names:
                label = task_name or "unnamed"
                errors.append(f"{display_path}:{line} registers unauthorized Celery task: {label}")
            elif task_name in module_discovered:
                errors.append(f"{display_path}:{line} registers duplicate Celery task: {task_name}")
            else:
                module_discovered.add(task_name)

    if require_complete_registry:
        for module_path, authorized_names in AUTHORIZED_TASKS_BY_MODULE.items():
            for missing_name in sorted(authorized_names - discovered.get(module_path, set())):
                errors.append(f"{module_path}: missing authorized Celery task: {missing_name}")
    return errors


def _scan_python_tree(root: Path, forbidden_imports: set[str]) -> list[str]:
    errors: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports = _top_level_imports(tree) & forbidden_imports
        if imports:
            errors.append(
                f"{path.relative_to(REPOSITORY_ROOT)} imports forbidden roots: "
                f"{', '.join(sorted(imports))}"
            )
    return errors


def _scan_symlinks() -> list[str]:
    errors: list[str] = []
    for path in SERVICE_ROOT.rglob("*"):
        if not path.is_symlink():
            continue
        target = path.resolve()
        if target == API_ROOT or target.is_relative_to(API_ROOT):
            errors.append(f"{path.relative_to(REPOSITORY_ROOT)} links into frozen api source")
    return errors


def _scan_dockerfile() -> list[str]:
    dockerfile = SERVICE_ROOT / "Dockerfile"
    if not dockerfile.is_file():
        return ["mem-knowledge/Dockerfile is missing"]
    errors: list[str] = []
    for line_number, raw_line in enumerate(
        dockerfile.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        normalized = line.lower()
        if normalized.startswith(("copy ", "add ")) and (
            " api" in f" {normalized}" or "api/uv.lock" in normalized
        ):
            errors.append(f"mem-knowledge/Dockerfile:{line_number} copies frozen api source")
    return errors


def main() -> int:
    errors = _scan_service_tree(SOURCE_ROOT, require_complete_registry=True)
    runtime_reports = _scan_runtime_reachability(
        SOURCE_ROOT,
        runtime_roots=RUNTIME_ROOT_MODULES,
        required_roots=REQUIRED_RUNTIME_ROOT_MODULES,
        dynamic_import_allowlist=RUNTIME_DYNAMIC_IMPORT_ALLOWLIST,
    )
    errors.extend(
        report
        for report in runtime_reports
        if not report.startswith("unreachable runtime module:")
    )
    for report in runtime_reports:
        if report.startswith("unreachable runtime module:"):
            print(f"runtime reachability report: {report}", file=sys.stderr)
    for package_root in PUBLIC_PACKAGE_ROOTS:
        errors.extend(_scan_python_tree(package_root, {"src"}))
    errors.extend(_scan_symlinks())
    errors.extend(_scan_dockerfile())
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print("knowledge service import boundaries passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
