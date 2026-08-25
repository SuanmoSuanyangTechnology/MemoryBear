"""Fail when the knowledge service crosses a frozen source boundary."""

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


def _top_level_imports(tree: ast.AST) -> set[str]:
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imports.add(node.module.split(".", 1)[0])
    return imports


def _parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}


def _direct_task_decorators(
    tree: ast.AST,
) -> tuple[list[tuple[int, str | None]], set[ast.Attribute], set[ast.expr]]:
    decorators: list[tuple[int, str | None]] = []
    task_attributes: set[ast.Attribute] = set()
    decorator_nodes: set[ast.expr] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            factory = decorator.func
            if not (
                isinstance(factory, ast.Attribute)
                and factory.attr == "task"
                and isinstance(factory.value, ast.Name)
                and factory.value.id == "celery_app"
            ):
                continue
            name_keywords = [keyword for keyword in decorator.keywords if keyword.arg == "name"]
            task_name: str | None = None
            if len(name_keywords) == 1:
                value = name_keywords[0].value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    task_name = value.value
            decorators.append((decorator.lineno, task_name))
            task_attributes.add(factory)
            decorator_nodes.add(decorator)
    return decorators, task_attributes, decorator_nodes


def _task_syntax_violations(tree: ast.AST) -> set[int]:
    _decorators, direct_task_attributes, direct_decorator_nodes = _direct_task_decorators(tree)
    violations: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                if decorator in direct_decorator_nodes or not isinstance(decorator, ast.Call):
                    continue
                for keyword in decorator.keywords:
                    value = keyword.value
                    if (
                        keyword.arg == "name"
                        and isinstance(value, ast.Constant)
                        and isinstance(value.value, str)
                        and value.value.startswith("app.core.rag.tasks.")
                    ):
                        violations.add(decorator.lineno)
        elif isinstance(node, ast.Attribute) and node.attr == "task":
            if node not in direct_task_attributes:
                violations.add(node.lineno)
        elif isinstance(node, ast.Attribute) and node.attr == "shared_task":
            violations.add(node.lineno)
        elif isinstance(node, ast.Name) and node.id == "shared_task":
            violations.add(node.lineno)
        elif isinstance(node, ast.Import):
            if any(alias.name.endswith(".shared_task") for alias in node.names):
                violations.add(node.lineno)
        elif isinstance(node, ast.ImportFrom):
            if any(alias.name == "shared_task" for alias in node.names):
                violations.add(node.lineno)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and node.args
        ):
            first_argument = node.args[0]
            if isinstance(first_argument, ast.Name) and first_argument.id == "celery_app":
                violations.add(node.lineno)
            if len(node.args) >= 2:
                attribute = node.args[1]
                if isinstance(attribute, ast.Constant) and attribute.value == "shared_task":
                    violations.add(node.lineno)
    return violations


def _task_decorators(tree: ast.AST) -> list[tuple[int, str | None]]:
    decorators, _task_attributes, _decorator_nodes = _direct_task_decorators(tree)
    decorated_lines = {line for line, _task_name in decorators}
    decorators.extend(
        (line, None) for line in sorted(_task_syntax_violations(tree) - decorated_lines)
    )
    return decorators


def _authorized_module_violations(tree: ast.AST) -> set[int]:
    parents = _parent_map(tree)
    _decorators, _task_attributes, direct_decorator_nodes = _direct_task_decorators(tree)
    violations = _task_syntax_violations(tree)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            violations.update(
                decorator.lineno
                for decorator in node.decorator_list
                if decorator not in direct_decorator_nodes
            )
        elif isinstance(node, ast.Name) and node.id == "getattr":
            parent = parents.get(node)
            if not (isinstance(parent, ast.Call) and parent.func is node):
                violations.add(node.lineno)
        elif isinstance(node, ast.ImportFrom) and node.module == "builtins":
            if any(alias.name == "getattr" for alias in node.names):
                violations.add(node.lineno)
        elif (
            isinstance(node, ast.Attribute)
            and node.attr == "getattr"
            and isinstance(node.value, ast.Name)
            and node.value.id == "builtins"
        ):
            violations.add(node.lineno)
    return violations


def _display_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(REPOSITORY_ROOT))
    except ValueError:
        return str(path.relative_to(root))


def _scan_service_tree(root: Path, *, require_complete_registry: bool = False) -> list[str]:
    """Validate imports and the exact authorized Celery task registration surface."""

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
        direct_decorators, _task_attributes, _decorator_nodes = _direct_task_decorators(tree)
        invalid_lines = _task_syntax_violations(tree)
        if module_path in AUTHORIZED_TASKS_BY_MODULE:
            invalid_lines.update(_authorized_module_violations(tree))
        for line in sorted(invalid_lines):
            errors.append(f"{display_path}:{line} registers unauthorized Celery task: unnamed")
        for line, task_name in direct_decorators:
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


def _scan_python_tree(
    root: Path,
    forbidden_imports: set[str],
    *,
    forbid_task_decorators: bool,
) -> list[str]:
    errors: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports = _top_level_imports(tree) & forbidden_imports
        if imports:
            errors.append(
                f"{path.relative_to(REPOSITORY_ROOT)} imports forbidden roots: "
                f"{', '.join(sorted(imports))}"
            )
        if forbid_task_decorators:
            for line, _task_name in _task_decorators(tree):
                errors.append(
                    f"{path.relative_to(REPOSITORY_ROOT)}:{line} registers an A2 business task"
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
    for package_root in PUBLIC_PACKAGE_ROOTS:
        errors.extend(
            _scan_python_tree(
                package_root,
                {"src"},
                forbid_task_decorators=False,
            )
        )
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
