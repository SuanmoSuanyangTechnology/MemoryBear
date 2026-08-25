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
TASK_MODULES = frozenset(AUTHORIZED_TASKS_BY_MODULE)
TASK_CAPABILITY_MODULES = TASK_MODULES | {
    "tasks/celery_app.py",
    "tasks/celery_worker.py",
    "tasks/dispatch.py",
}
TASK_CAPABILITY_MODULE_NAMES = frozenset(Path(module).stem for module in TASK_CAPABILITY_MODULES)
WORKER_TASK_MODULE_NAMES = frozenset({"document", "evidence_graph", "legacy_compat", "qa_import"})
SAFE_CELERY_IMPORTS = {
    ("tasks/celery_app.py", "celery"): frozenset({"Celery"}),
    ("tasks/celery_worker.py", "celery.signals"): frozenset(
        {"worker_process_init", "worker_process_shutdown"}
    ),
    ("tasks/evidence_graph.py", "celery"): frozenset({"states"}),
    ("tasks/evidence_graph.py", "celery.exceptions"): frozenset({"Ignore", "Retry"}),
}
CELERY_APP_IMPORT_NAMES = {
    **{module: frozenset({"celery_app"}) for module in TASK_MODULES},
    "tasks/dispatch.py": frozenset({"PUBLISHABLE_KNOWLEDGE_TASK_ROUTES", "celery_app"}),
    "tasks/celery_worker.py": frozenset({"celery_app"}),
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
            if (
                not decorator.args
                and all(keyword.arg is not None for keyword in decorator.keywords)
                and len(name_keywords) == 1
            ):
                value = name_keywords[0].value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    task_name = value.value
            decorators.append((decorator.lineno, task_name))
            task_attributes.add(factory)
            decorator_nodes.add(decorator)
    return decorators, task_attributes, decorator_nodes


def _direct_decorator_nodes(tree: ast.AST) -> tuple[set[ast.Attribute], set[ast.Name]]:
    _decorators, attributes, _calls = _direct_task_decorators(tree)
    return attributes, {
        attribute.value for attribute in attributes if isinstance(attribute.value, ast.Name)
    }


def _is_exact_celery_app_import(node: ast.ImportFrom, module_path: str) -> bool:
    expected_names = CELERY_APP_IMPORT_NAMES.get(module_path)
    if expected_names is None or node.level != 1 or node.module != "celery_app":
        return False
    return (
        frozenset(alias.name for alias in node.names) == expected_names
        and len(node.names) == len(expected_names)
        and all(alias.asname is None for alias in node.names)
    )


def _is_exact_worker_task_import(node: ast.ImportFrom, module_path: str) -> bool:
    return (
        module_path == "tasks/celery_worker.py"
        and node.level == 1
        and node.module is None
        and frozenset(alias.name for alias in node.names) == WORKER_TASK_MODULE_NAMES
        and len(node.names) == len(WORKER_TASK_MODULE_NAMES)
        and all(alias.asname is None for alias in node.names)
    )


def _is_exact_dispatch_type_import(node: ast.ImportFrom) -> bool:
    module_parts = (node.module or "").split(".")
    return (
        module_parts[-2:] == ["tasks", "dispatch"]
        and len(node.names) == 1
        and node.names[0].name == "TaskDispatcher"
        and node.names[0].asname is None
    )


def _is_exact_safe_celery_import(node: ast.ImportFrom, module_path: str) -> bool:
    expected_names = SAFE_CELERY_IMPORTS.get((module_path, node.module or ""))
    return (
        expected_names is not None
        and node.level == 0
        and frozenset(alias.name for alias in node.names) == expected_names
        and len(node.names) == len(expected_names)
        and all(alias.asname is None for alias in node.names)
    )


def _contains_task_capability_module(parts: list[str]) -> bool:
    return any(
        first == "tasks" and second in TASK_CAPABILITY_MODULE_NAMES
        for first, second in zip(parts, parts[1:], strict=False)
    )


def _imports_celery_app_capability(node: ast.Import | ast.ImportFrom) -> bool:
    if isinstance(node, ast.Import):
        return any(
            _contains_task_capability_module(alias.name.split("."))
            or alias.name.split(".", 1)[0] == "celery"
            for alias in node.names
        )
    module_parts = (node.module or "").split(".")
    if module_parts == ["celery_app"] or _contains_task_capability_module(module_parts):
        return True
    if (
        module_parts
        and module_parts[-1] == "tasks"
        and any(alias.name in TASK_CAPABILITY_MODULE_NAMES for alias in node.names)
    ):
        return True
    if module_parts and module_parts[0] == "celery":
        return True
    return False


def _is_celery_app_definition(node: ast.Name, parents: dict[ast.AST, ast.AST]) -> bool:
    assignment = parents.get(node)
    if not (
        isinstance(assignment, ast.Assign)
        and len(assignment.targets) == 1
        and assignment.targets[0] is node
    ):
        return False
    value = assignment.value
    return (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id == "create_celery_app"
        and len(value.args) == 1
        and not value.keywords
        and isinstance(value.args[0], ast.Call)
        and isinstance(value.args[0].func, ast.Name)
        and value.args[0].func.id == "get_settings"
        and not value.args[0].args
        and not value.args[0].keywords
    )


def _dispatch_default_nodes(tree: ast.AST) -> set[ast.Name]:
    allowed: set[ast.Name] = set()
    for class_node in tree.body if isinstance(tree, ast.Module) else ():
        if not isinstance(class_node, ast.ClassDef) or class_node.name != "TaskDispatcher":
            continue
        for function in class_node.body:
            if not isinstance(function, ast.FunctionDef) or function.name != "__init__":
                continue
            positional = [*function.args.posonlyargs, *function.args.args]
            defaults = [None] * (len(positional) - len(function.args.defaults)) + list(
                function.args.defaults
            )
            for argument, default in zip(positional, defaults, strict=True):
                if (
                    argument.arg == "application"
                    and isinstance(default, ast.Name)
                    and default.id == "celery_app"
                ):
                    allowed.add(default)
    return allowed


def _inside_top_level_function(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
    function_name: str,
) -> bool:
    current = parents.get(node)
    while current is not None and not isinstance(current, ast.FunctionDef):
        current = parents.get(current)
    return (
        isinstance(current, ast.FunctionDef)
        and current.name == function_name
        and isinstance(parents.get(current), ast.Module)
    )


def _is_allowed_celery_class_load(
    node: ast.Name,
    parents: dict[ast.AST, ast.AST],
) -> bool:
    if not _inside_top_level_function(node, parents, "create_celery_app"):
        return False
    parent = parents.get(node)
    if isinstance(parent, ast.Call) and parent.func is node:
        return True
    function = parent
    while function is not None and not isinstance(function, ast.FunctionDef):
        function = parents.get(function)
    return isinstance(function, ast.FunctionDef) and function.returns is node


def _celery_capability_violations(tree: ast.AST, module_path: str) -> set[int]:
    parents = _parent_map(tree)
    direct_attributes, direct_app_names = _direct_decorator_nodes(tree)
    dispatch_defaults = (
        _dispatch_default_nodes(tree) if module_path == "tasks/dispatch.py" else set()
    )
    violations: set[int] = set()
    celery_import_lines: list[int] = []

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                if any(
                    keyword.arg == "name"
                    and isinstance(keyword.value, ast.Constant)
                    and isinstance(keyword.value.value, str)
                    and keyword.value.value.startswith("app.core.rag.tasks.")
                    for keyword in decorator.keywords
                ) and not (
                    isinstance(decorator.func, ast.Attribute)
                    and decorator.func in direct_attributes
                ):
                    violations.add(decorator.lineno)
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.ImportFrom) and _is_exact_celery_app_import(node, module_path):
                celery_import_lines.append(node.lineno)
                continue
            if isinstance(node, ast.ImportFrom) and _is_exact_worker_task_import(node, module_path):
                continue
            if isinstance(node, ast.ImportFrom) and _is_exact_dispatch_type_import(node):
                continue
            if isinstance(node, ast.ImportFrom) and _is_exact_safe_celery_import(node, module_path):
                continue
            if (
                isinstance(node, ast.ImportFrom)
                and module_path.startswith("tasks/")
                and node.level == 1
                and node.module is None
                and any(alias.name in TASK_CAPABILITY_MODULE_NAMES for alias in node.names)
            ):
                violations.add(node.lineno)
            if _imports_celery_app_capability(node):
                violations.add(node.lineno)
        elif isinstance(node, ast.Attribute):
            if node.attr == "task" and node not in direct_attributes:
                violations.add(node.lineno)
            elif node.attr in {"celery_app", "shared_task"}:
                violations.add(node.lineno)
        elif isinstance(node, ast.Name):
            if node.id == "celery_app":
                if isinstance(node.ctx, ast.Load) and (
                    node in direct_app_names or node in dispatch_defaults
                ):
                    continue
                if (
                    module_path == "tasks/celery_app.py"
                    and isinstance(node.ctx, ast.Store)
                    and _is_celery_app_definition(node, parents)
                ):
                    continue
                violations.add(node.lineno)
            elif node.id == "shared_task":
                violations.add(node.lineno)
            elif node.id == "Celery" and isinstance(node.ctx, ast.Load):
                if not (
                    module_path == "tasks/celery_app.py"
                    and _is_allowed_celery_class_load(node, parents)
                ):
                    violations.add(node.lineno)
            elif module_path in TASK_MODULES and node.id == "getattr":
                parent = parents.get(node)
                if not (isinstance(parent, ast.Call) and parent.func is node):
                    violations.add(node.lineno)
        elif isinstance(node, ast.Constant) and node.value in {"task", "shared_task"}:
            violations.add(node.lineno)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == "celery_app":
                violations.add(node.lineno)
        elif isinstance(node, ast.arg) and node.arg == "celery_app":
            violations.add(node.lineno)

    if len(celery_import_lines) > 1:
        violations.update(celery_import_lines[1:])
    return violations


def _task_syntax_violations(tree: ast.AST) -> set[int]:
    direct_decorators, direct_attributes, _decorator_nodes = _direct_task_decorators(tree)
    direct_lines = {line for line, _name in direct_decorators}
    violations: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "task"
            and node not in direct_attributes
        ):
            violations.add(node.lineno)
        elif isinstance(node, ast.Attribute) and node.attr in {"shared_task", "celery_app"}:
            violations.add(node.lineno)
        elif isinstance(node, ast.Name) and node.id in {"shared_task", "celery_app"}:
            parent = _parent_map(tree).get(node)
            if not (
                isinstance(parent, ast.Attribute)
                and parent in direct_attributes
                and parent.value is node
            ):
                violations.add(node.lineno)
        elif isinstance(node, (ast.Import, ast.ImportFrom)) and _imports_celery_app_capability(
            node
        ):
            violations.add(node.lineno)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) or decorator.lineno in direct_lines:
                    continue
                if any(
                    keyword.arg == "name"
                    and isinstance(keyword.value, ast.Constant)
                    and isinstance(keyword.value.value, str)
                    and keyword.value.value.startswith("app.core.rag.tasks.")
                    for keyword in decorator.keywords
                ):
                    violations.add(decorator.lineno)
    return violations


def _task_decorators(tree: ast.AST) -> list[tuple[int, str | None]]:
    decorators, _task_attributes, _decorator_nodes = _direct_task_decorators(tree)
    decorated_lines = {line for line, _task_name in decorators}
    decorators.extend(
        (line, None) for line in sorted(_task_syntax_violations(tree) - decorated_lines)
    )
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
        direct_decorators, _task_attributes, _decorator_nodes = _direct_task_decorators(tree)
        invalid_lines = _celery_capability_violations(tree, module_path)
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
        for module_path in CELERY_APP_IMPORT_NAMES:
            path = root / module_path
            if not path.is_file():
                errors.append(f"{module_path}: missing celery_app capability import")
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imports = [
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
                and _is_exact_celery_app_import(node, module_path)
            ]
            if len(imports) != 1:
                errors.append(f"{module_path}: missing celery_app capability import")
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
