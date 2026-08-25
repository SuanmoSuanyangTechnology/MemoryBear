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


def _assignment_pairs(target: ast.expr, value: ast.expr) -> list[tuple[str, ast.expr]]:
    if isinstance(target, ast.Name):
        return [(target.id, value)]
    if isinstance(target, ast.Starred):
        return _assignment_pairs(target.value, value)
    if isinstance(target, (ast.Tuple, ast.List)):
        if isinstance(value, (ast.Tuple, ast.List)) and len(target.elts) == len(value.elts):
            return [
                pair
                for nested_target, nested_value in zip(target.elts, value.elts, strict=True)
                for pair in _assignment_pairs(nested_target, nested_value)
            ]
        return [
            pair
            for nested_target in target.elts
            for pair in _assignment_pairs(nested_target, value)
        ]
    return []


def _all_assignment_pairs(tree: ast.AST) -> list[tuple[str, ast.expr]]:
    pairs: list[tuple[str, ast.expr]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                pairs.extend(_assignment_pairs(target, node.value))
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            pairs.extend(_assignment_pairs(node.target, node.value))
    return pairs


def _task_factory_aliases(
    tree: ast.AST,
) -> tuple[dict[str, tuple[str | None, bool]], set[str], set[str], set[str], set[str]]:
    factories: dict[str, tuple[str | None, bool]] = {"shared_task": (None, False)}
    celery_modules: set[str] = set()
    task_owners = {"celery_app"}
    partial_functions = {"partial"}
    functools_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "celery":
                    celery_modules.add(alias.asname or alias.name)
                elif alias.name == "functools":
                    functools_modules.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imported_name = alias.asname or alias.name
                if node.module == "celery" and alias.name == "shared_task":
                    factories[imported_name] = (None, False)
                elif node.module == "functools" and alias.name == "partial":
                    partial_functions.add(imported_name)
                elif alias.name == "celery_app":
                    task_owners.add(imported_name)

    def is_partial(expression: ast.expr) -> bool:
        return (
            isinstance(expression, ast.Name)
            and expression.id in partial_functions
            or (
                isinstance(expression, ast.Attribute)
                and expression.attr == "partial"
                and isinstance(expression.value, ast.Name)
                and expression.value.id in functools_modules
            )
        )

    def factory_info(expression: ast.expr) -> tuple[bool, str | None, bool]:
        if isinstance(expression, ast.Name) and expression.id in factories:
            bound_name, uncertain = factories[expression.id]
            return True, bound_name, uncertain
        if isinstance(expression, ast.Attribute):
            if expression.attr == "task":
                return True, None, False
            if (
                expression.attr == "shared_task"
                and isinstance(expression.value, ast.Name)
                and expression.value.id in celery_modules
            ):
                return True, None, False
        if (
            isinstance(expression, ast.Call)
            and isinstance(expression.func, ast.Name)
            and expression.func.id == "getattr"
            and len(expression.args) >= 2
            and isinstance(expression.args[0], ast.Name)
        ):
            owner = expression.args[0].id
            attribute = expression.args[1]
            attribute_name = (
                attribute.value
                if isinstance(attribute, ast.Constant) and isinstance(attribute.value, str)
                else None
            )
            if owner in task_owners and attribute_name in {None, "task"}:
                return True, None, attribute_name is None
            if owner in celery_modules and attribute_name in {None, "shared_task"}:
                return True, None, attribute_name is None
        if isinstance(expression, ast.Call) and is_partial(expression.func) and expression.args:
            is_factory, bound_name, uncertain = factory_info(expression.args[0])
            if is_factory:
                for keyword in expression.keywords:
                    if keyword.arg != "name":
                        continue
                    if isinstance(keyword.value, ast.Constant) and isinstance(
                        keyword.value.value, str
                    ):
                        bound_name = keyword.value.value
                    else:
                        bound_name = None
                        uncertain = True
                return True, bound_name, uncertain
        return False, None, False

    def merge_factory(name: str, bound_name: str | None, uncertain: bool) -> bool:
        candidate = (bound_name, uncertain)
        current = factories.get(name)
        if current is None:
            factories[name] = candidate
            return True
        if current == candidate or current == (None, True):
            return False
        factories[name] = (None, True)
        return True

    changed = True
    while changed:
        changed = False
        for name, value in _all_assignment_pairs(tree):
            is_celery_module = isinstance(value, ast.Name) and value.id in celery_modules
            is_task_owner = isinstance(value, ast.Name) and value.id in task_owners
            is_functools_module = isinstance(value, ast.Name) and value.id in functools_modules
            is_factory, bound_name, uncertain = factory_info(value)
            if is_factory and merge_factory(name, bound_name, uncertain):
                changed = True
            if is_celery_module and name not in celery_modules:
                celery_modules.add(name)
                changed = True
            if is_task_owner and name not in task_owners:
                task_owners.add(name)
                changed = True
            if is_partial(value) and name not in partial_functions:
                partial_functions.add(name)
                changed = True
            if is_functools_module and name not in functools_modules:
                functools_modules.add(name)
                changed = True
    return factories, celery_modules, task_owners, partial_functions, functools_modules


def _task_decorators(tree: ast.AST) -> list[tuple[int, str | None]]:
    decorators: list[tuple[int, str | None]] = []
    factories, celery_modules, task_owners, partial_functions, functools_modules = (
        _task_factory_aliases(tree)
    )

    def is_partial(expression: ast.expr) -> bool:
        return (
            isinstance(expression, ast.Name)
            and expression.id in partial_functions
            or (
                isinstance(expression, ast.Attribute)
                and expression.attr == "partial"
                and isinstance(expression.value, ast.Name)
                and expression.value.id in functools_modules
            )
        )

    def factory_info(expression: ast.expr) -> tuple[bool, str | None, bool]:
        if isinstance(expression, ast.Name) and expression.id in factories:
            bound_name, uncertain = factories[expression.id]
            return True, bound_name, uncertain
        if isinstance(expression, ast.Attribute) and expression.attr == "task":
            return True, None, False
        if (
            isinstance(expression, ast.Attribute)
            and expression.attr == "shared_task"
            and isinstance(expression.value, ast.Name)
            and expression.value.id in celery_modules
        ):
            return True, None, False
        if (
            isinstance(expression, ast.Call)
            and isinstance(expression.func, ast.Name)
            and expression.func.id == "getattr"
            and len(expression.args) >= 2
            and isinstance(expression.args[0], ast.Name)
        ):
            owner = expression.args[0].id
            attribute = expression.args[1]
            attribute_name = (
                attribute.value
                if isinstance(attribute, ast.Constant) and isinstance(attribute.value, str)
                else None
            )
            if owner in task_owners and attribute_name in {None, "task"}:
                return True, None, attribute_name is None
            if owner in celery_modules and attribute_name in {None, "shared_task"}:
                return True, None, attribute_name is None
        if isinstance(expression, ast.Call) and is_partial(expression.func) and expression.args:
            is_factory, bound_name, uncertain = factory_info(expression.args[0])
            if is_factory:
                for keyword in expression.keywords:
                    if keyword.arg != "name":
                        continue
                    if isinstance(keyword.value, ast.Constant) and isinstance(
                        keyword.value.value, str
                    ):
                        bound_name = keyword.value.value
                    else:
                        bound_name = None
                        uncertain = True
                return True, bound_name, uncertain
        return False, None, False

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            is_factory, task_name, uncertain = factory_info(target)
            if not is_factory:
                is_factory, task_name, uncertain = factory_info(decorator)
            if not is_factory:
                continue
            if isinstance(decorator, ast.Call):
                for keyword in decorator.keywords:
                    if keyword.arg != "name":
                        continue
                    if isinstance(keyword.value, ast.Constant) and isinstance(
                        keyword.value.value, str
                    ):
                        task_name = keyword.value.value
                    else:
                        task_name = None
                        uncertain = True
            if uncertain:
                task_name = None
            decorators.append((decorator.lineno, task_name))
    return decorators


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
