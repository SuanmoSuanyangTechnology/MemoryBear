"""Fail when the A2 service crosses a frozen source boundary."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SERVICE_ROOT = REPOSITORY_ROOT / "mem-knowledge"
SOURCE_ROOT = SERVICE_ROOT / "src" / "mem_knowledge"
API_ROOT = (REPOSITORY_ROOT / "api").resolve()
PUBLIC_PACKAGE_ROOTS = (
    REPOSITORY_ROOT / "packages" / "redbear-model" / "src",
    REPOSITORY_ROOT / "packages" / "storage-core" / "src",
)
FORBIDDEN_SERVICE_IMPORTS = {"api", "app", "premium"}


def _top_level_imports(tree: ast.AST) -> set[str]:
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imports.add(node.module.split(".", 1)[0])
    return imports


def _task_decorators(tree: ast.AST) -> list[int]:
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            is_task = isinstance(target, ast.Attribute) and target.attr == "task"
            is_shared_task = isinstance(target, ast.Name) and target.id == "shared_task"
            if is_task or is_shared_task:
                lines.append(decorator.lineno)
    return lines


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
            for line in _task_decorators(tree):
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
            errors.append(
                f"{path.relative_to(REPOSITORY_ROOT)} links into frozen api source"
            )
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
            errors.append(
                f"mem-knowledge/Dockerfile:{line_number} copies frozen api source"
            )
    return errors


def main() -> int:
    errors = _scan_python_tree(
        SOURCE_ROOT,
        FORBIDDEN_SERVICE_IMPORTS,
        forbid_task_decorators=True,
    )
    for package_root in PUBLIC_PACKAGE_ROOTS:
        errors.extend(
            _scan_python_tree(
                package_root,
                {"mem_knowledge"},
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
