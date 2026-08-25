"""Scan knowledge worker source and artifacts for removed runtime dependencies."""

from __future__ import annotations

import argparse
import ast
import re
import stat
import sys
import zipfile
from pathlib import Path, PurePosixPath

FORBIDDEN_DISTRIBUTIONS = {
    "graspologic",
    "networkx",
    "onnxruntime",
    "onnxruntime-gpu",
    "opencv-python",
    "opencv-python-headless",
    "torch",
    "xgboost",
}
FORBIDDEN_IMPORT_ROOTS = {
    "cv2": "opencv-python",
    "graspologic": "graspologic",
    "networkx": "networkx",
    "onnxruntime": "onnxruntime",
    "torch": "torch",
    "xgboost": "xgboost",
}
FORBIDDEN_EXECUTABLE_MARKERS = {
    "init_graphrag",
    "load_legacy_graph",
    "run_graphrag",
}
LEGACY_TASK_NAMES = {
    "app.core.rag.tasks.build_graphrag_for_document",
    "app.core.rag.tasks.build_graphrag_for_kb",
    "app.core.rag.tasks.migrate_evidence_graph_knowledge",
}
LEGACY_TASK_MARKERS = {task_name.rsplit(".", 1)[-1] for task_name in LEGACY_TASK_NAMES}
LEGACY_TASK_ALLOWED_PATHS = {
    "src/tasks/celery_app.py",
    "src/tasks/legacy_compat.py",
    "tasks/celery_app.py",
    "tasks/legacy_compat.py",
}


def _normalize_distribution(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _path_label(path: str | PurePosixPath) -> str:
    return PurePosixPath(path).as_posix().lstrip("/") or "."


def _forbidden_path_marker(path: str | PurePosixPath) -> str | None:
    parts = [part.lower() for part in PurePosixPath(path).parts if part not in {"", "/"}]
    normalized_parts = [_normalize_distribution(part) for part in parts]
    for index, part in enumerate(parts):
        if "deepdoc" in part:
            return "deepdoc"
        if part in {"plain_pdf", "plainpdf"}:
            return part
        if part == "graphrag":
            return "graphrag"
        normalized = normalized_parts[index]
        if normalized in FORBIDDEN_DISTRIBUTIONS:
            return normalized
    for first, second in zip(parts, parts[1:], strict=False):
        if (first, second) in {("api", "app"), ("rag", "app")}:
            return f"{first}.{second}"
    return None


def _import_names(tree: ast.AST) -> set[str]:
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imports.add(node.module)
    return imports


def _forbidden_import_marker(module: str) -> str | None:
    lowered = module.lower()
    parts = lowered.split(".")
    if any("deepdoc" in part for part in parts):
        return "deepdoc"
    if lowered == "app" or lowered.startswith("app."):
        return "app"
    if lowered == "api.app" or lowered.startswith("api.app."):
        return "api.app"
    if any(
        first == "rag" and second == "app" for first, second in zip(parts, parts[1:], strict=False)
    ):
        return "rag.app"
    if any(part in {"plain_pdf", "plainpdf"} for part in parts):
        return "plain_pdf"
    if "graphrag" in parts:
        return "graphrag"
    return FORBIDDEN_IMPORT_ROOTS.get(parts[0])


def _legacy_task_allowed(path: str | PurePosixPath) -> bool:
    normalized = _path_label(path)
    return normalized in LEGACY_TASK_ALLOWED_PATHS


def _assigned_names(target: ast.expr) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        return {name for item in target.elts for name in _assigned_names(item)}
    return set()


def _dynamic_import_aliases(tree: ast.AST) -> tuple[set[str], set[str]]:
    loaders = {"__import__"}
    importlib_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "importlib":
                    importlib_modules.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "importlib":
            for alias in node.names:
                if alias.name == "import_module":
                    loaders.add(alias.asname or alias.name)

    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                targets = {name for target in node.targets for name in _assigned_names(target)}
                value = node.value
            elif isinstance(node, ast.AnnAssign):
                targets = _assigned_names(node.target)
                value = node.value
            else:
                continue
            if value is None:
                continue
            is_importlib_module = isinstance(value, ast.Name) and value.id in importlib_modules
            is_loader = isinstance(value, ast.Name) and value.id in loaders
            if (
                isinstance(value, ast.Attribute)
                and value.attr == "import_module"
                and isinstance(value.value, ast.Name)
                and value.value.id in importlib_modules
            ):
                is_loader = True
            for name in targets:
                if is_loader and name not in loaders:
                    loaders.add(name)
                    changed = True
                if is_importlib_module and name not in importlib_modules:
                    importlib_modules.add(name)
                    changed = True
    return loaders, importlib_modules


def _scan_python_text(path: str, source: str) -> list[str]:
    label = _path_label(path)
    errors: list[str] = []
    try:
        tree = ast.parse(source, filename=label)
    except (SyntaxError, ValueError):
        return [f"{label}: invalid-python"]
    for module in sorted(_import_names(tree)):
        marker = _forbidden_import_marker(module)
        if marker:
            errors.append(f"{label}: {marker}")
    dynamic_loaders, importlib_modules = _dynamic_import_aliases(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        is_dynamic_import = isinstance(function, ast.Name) and function.id in dynamic_loaders
        if (
            isinstance(function, ast.Attribute)
            and function.attr == "import_module"
            and isinstance(function.value, ast.Name)
            and function.value.id in importlib_modules
        ):
            is_dynamic_import = True
        if not is_dynamic_import:
            continue
        first_argument: ast.expr | None = node.args[0] if node.args else None
        if first_argument is None:
            first_argument = next(
                (keyword.value for keyword in node.keywords if keyword.arg == "name"),
                None,
            )
        if (
            first_argument is None
            or not isinstance(first_argument, ast.Constant)
            or not isinstance(first_argument.value, str)
        ):
            errors.append(f"{label}: dynamic-import")
            continue
        marker = _forbidden_import_marker(first_argument.value.strip())
        if marker:
            errors.append(f"{label}: {marker}")
    lowered = source.lower()
    for marker in sorted(FORBIDDEN_EXECUTABLE_MARKERS):
        if marker in lowered:
            errors.append(f"{label}: {marker}")
    if not _legacy_task_allowed(label):
        for marker in sorted(LEGACY_TASK_MARKERS):
            if marker in lowered:
                errors.append(f"{label}: {marker}")
    return errors


def _bytecode_source(path: Path) -> Path:
    if path.parent.name == "__pycache__":
        module_name = path.name.split(".", 1)[0]
        return path.parent.parent / f"{module_name}.py"
    return path.with_suffix(".py")


def _scan_static_pth_target(root: Path, pth: Path, raw_target: str) -> list[str]:
    label = _path_label(pth.relative_to(root))
    errors: list[str] = []
    raw_marker = _forbidden_path_marker(raw_target)
    if raw_marker:
        errors.append(f"{label}: {raw_marker}")
    target = Path(raw_target)
    if not target.is_absolute():
        target = pth.parent / target
    if target.is_symlink():
        errors.append(f"{label}: symlink")
        return errors
    try:
        resolved = target.resolve()
    except OSError:
        return errors
    resolved_marker = _forbidden_path_marker(resolved.as_posix())
    if resolved_marker:
        errors.append(f"{label}: {resolved_marker}")
    if not resolved.exists():
        return errors
    if resolved.is_file() and resolved.suffix == ".py":
        try:
            errors.extend(_scan_python_text(label, resolved.read_text(encoding="utf-8")))
        except (OSError, UnicodeError):
            errors.append(f"{label}: unreadable-python")
    elif resolved.is_dir():
        source_root = resolved / "src" if (resolved / "src").is_dir() else resolved
        errors.extend(f"{label}: {error}" for error in _scan_source(source_root))
    return errors


def _scan_pth_files(root: Path) -> list[str]:
    errors: list[str] = []
    for pth in sorted(root.glob("*.pth")):
        label = _path_label(pth.relative_to(root))
        if pth.is_symlink():
            errors.append(f"{label}: symlink")
            continue
        try:
            lines = pth.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            errors.append(f"{label}: unreadable-pth")
            continue
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped == "import" or stripped.startswith(("import ", "import\t")):
                errors.append(f"{label}: executable-pth")
                continue
            errors.extend(_scan_static_pth_target(root, pth, stripped))
    return errors


def _scan_source(root: Path) -> list[str]:
    errors: list[str] = []
    if not root.is_dir():
        return [f"{_path_label(root.name)}: missing-source"]
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if path.is_symlink():
            errors.append(f"{_path_label(relative)}: symlink")
            continue
        marker = _forbidden_path_marker(relative)
        if marker:
            errors.append(f"{_path_label(relative)}: {marker}")
        if path.is_file() and path.suffix == ".py":
            try:
                source = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                errors.append(f"{_path_label(relative)}: unreadable-python")
                continue
            errors.extend(_scan_python_text(_path_label(relative), source))
    return errors


def _scan_wheel(path: Path) -> list[str]:
    if not path.is_file():
        return [f"{_path_label(path.name)}: missing-wheel"]
    errors: list[str] = []
    try:
        with zipfile.ZipFile(path) as archive:
            for member_info in sorted(archive.infolist(), key=lambda item: item.filename):
                member = member_info.filename
                mode = member_info.external_attr >> 16
                if stat.S_ISLNK(mode):
                    errors.append(f"{_path_label(member)}: symlink")
                    continue
                suffix = PurePosixPath(member).suffix.lower()
                if suffix in {".pyc", ".pyo"}:
                    errors.append(f"{_path_label(member)}: bytecode")
                    continue
                if suffix == ".pth":
                    try:
                        lines = archive.read(member).decode("utf-8").splitlines()
                    except (KeyError, UnicodeError, OSError):
                        errors.append(f"{_path_label(member)}: unreadable-pth")
                        continue
                    for line in lines:
                        stripped = line.strip()
                        if not stripped or stripped.startswith("#"):
                            continue
                        if stripped == "import" or stripped.startswith(("import ", "import\t")):
                            errors.append(f"{_path_label(member)}: executable-pth")
                            continue
                        path_marker = _forbidden_path_marker(stripped)
                        if path_marker:
                            errors.append(f"{_path_label(member)}: {path_marker}")
                marker = _forbidden_path_marker(member)
                if marker:
                    errors.append(f"{_path_label(member)}: {marker}")
                if member.endswith(".py"):
                    try:
                        source = archive.read(member).decode("utf-8")
                    except (KeyError, UnicodeError, OSError):
                        errors.append(f"{_path_label(member)}: unreadable-python")
                        continue
                    errors.extend(_scan_python_text(member, source))
    except (OSError, zipfile.BadZipFile):
        return [f"{_path_label(path.name)}: invalid-wheel"]
    return errors


def _metadata_distribution_name(metadata: Path) -> str | None:
    try:
        with metadata.open(encoding="utf-8", errors="replace") as stream:
            for line in stream:
                if not line.strip():
                    break
                if line.lower().startswith("name:"):
                    return _normalize_distribution(line.split(":", 1)[1].strip())
    except OSError:
        return None
    return None


def _scan_site_packages(root: Path) -> list[str]:
    if not root.is_dir():
        return [f"{_path_label(root.name)}: missing-site-packages"]
    errors: list[str] = []
    errors.extend(_scan_pth_files(root))
    for metadata in sorted(root.glob("*.dist-info/METADATA")):
        distribution = _metadata_distribution_name(metadata)
        if distribution in FORBIDDEN_DISTRIBUTIONS:
            errors.append(distribution)
    for child in sorted(root.iterdir()):
        if child.is_symlink():
            errors.append(f"{_path_label(child.name)}: symlink")
            continue
        marker = _forbidden_path_marker(child.name)
        if marker:
            errors.append(f"{_path_label(child.name)}: {marker}")
    service_source = root / "src"
    if service_source.is_dir():
        errors.extend(f"src/{error}" for error in _scan_source(service_source))
    for bytecode in sorted(
        path for path in root.rglob("*") if path.suffix.lower() in {".pyc", ".pyo"}
    ):
        if not _bytecode_source(bytecode).is_file():
            errors.append(f"{_path_label(bytecode.relative_to(root))}: orphan-bytecode")
    for symlink in sorted(path for path in root.rglob("*") if path.is_symlink()):
        errors.append(f"{_path_label(symlink.relative_to(root))}: symlink")
    return errors


def _scan_rootfs(root: Path) -> list[str]:
    if not root.is_dir():
        return [f"{_path_label(root.name)}: missing-rootfs"]
    errors: list[str] = []
    service_source = root / "code" / "mem-knowledge" / "src"
    if not service_source.is_dir():
        errors.append("rootfs: missing-source")
    elif service_source.is_symlink():
        errors.append("code/mem-knowledge/src: symlink")
    else:
        errors.extend(f"code/mem-knowledge/src/{error}" for error in _scan_source(service_source))
    site_package_roots = [path for path in sorted(root.rglob("site-packages")) if path.is_dir()]
    if not site_package_roots:
        errors.append("rootfs: missing-site-packages")
    for site_packages in site_package_roots:
        prefix = _path_label(site_packages.relative_to(root))
        if site_packages.is_symlink():
            errors.append(f"{prefix}: symlink")
        else:
            errors.extend(f"{prefix}/{error}" for error in _scan_site_packages(site_packages))
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        marker = _forbidden_path_marker(relative)
        if marker:
            errors.append(f"{_path_label(relative)}: {marker}")
    return errors


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--source", type=Path)
    modes.add_argument("--wheel", type=Path)
    modes.add_argument("--site-packages", type=Path)
    modes.add_argument("--rootfs", type=Path)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.source is not None:
        errors = _scan_source(args.source)
    elif args.wheel is not None:
        errors = _scan_wheel(args.wheel)
    elif args.site_packages is not None:
        errors = _scan_site_packages(args.site_packages)
    else:
        errors = _scan_rootfs(args.rootfs)
    unique_errors = sorted(set(errors))
    if unique_errors:
        for error in unique_errors:
            print(error, file=sys.stderr)
        return 1
    print("knowledge worker artifact boundary passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
