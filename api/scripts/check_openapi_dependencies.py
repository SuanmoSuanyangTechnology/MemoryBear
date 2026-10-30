"""Reject schema-export dependency drift from each checkout's production metadata."""

from pathlib import Path
import tomllib

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


# Explicit runtime-only roots omitted from requirements-openapi.txt.
EXCLUDED_ROOTS = frozenset({
    "torch", "onnxruntime", "opencv-python", "xgboost", "modelscope",
    "graspologic", "matplotlib", "scikit-learn", "scipy", "shapely",
    "pyclipper", "pdfplumber", "huggingface-hub", "python-pptx", "pypdf",
    "tika", "mammoth", "python-calamine", "xlrd", "demjson3", "editdistance",
    "pytest", "pytest-asyncio", "flower", "volcengine-python-sdk", "pymupdf",
})
FORBIDDEN_PACKAGES = frozenset({
    "torch", "onnxruntime", "onnxruntime-gpu", "opencv-python",
    "opencv-python-headless", "xgboost", "modelscope", "transformers",
    "sentence-transformers", "graspologic", "scikit-learn", "scipy", "matplotlib",
})


def local_source_paths(project):
    """Workspace packages that ``uv --no-emit-local`` omits, keyed by canonical name."""
    sources = project.get("tool", {}).get("uv", {}).get("sources", {})
    return {
        canonicalize_name(name): source["path"]
        for name, source in sources.items()
        if "path" in source
    }


def validate(api_dir: Path):
    lock = tomllib.loads((api_dir / "uv.lock").read_text())
    project = tomllib.loads((api_dir / "pyproject.toml").read_text())
    locked_versions = {}
    for package in lock["package"]:
        locked_versions.setdefault(canonicalize_name(package["name"]), set()).add(package["version"])
    declared = {}
    for text in project["project"]["dependencies"]:
        requirement = Requirement(text)
        if requirement.marker is None or requirement.marker.evaluate():
            declared[canonicalize_name(requirement.name)] = requirement

    local_sources = local_source_paths(project)
    local_paths = set(local_sources.values())
    selected = {}
    included_local = set()
    errors = []
    for line in (api_dir / "requirements-openapi.txt").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("./", "../", "/")):
            if line in local_paths:
                included_local.add(line)
            else:
                errors.append(f"{line}: local source is not declared in [tool.uv.sources]")
            continue
        requirement = Requirement(line)
        if requirement.marker is not None and not requirement.marker.evaluate():
            continue
        name = canonicalize_name(requirement.name)
        if name in FORBIDDEN_PACKAGES:
            errors.append(f"{name}: heavy package forbidden in export environment")
        specifiers = list(requirement.specifier)
        if len(specifiers) != 1 or specifiers[0].operator != "==" or "*" in specifiers[0].version:
            errors.append(f"{name}: export version must be exactly pinned")
            continue
        version = specifiers[0].version
        selected[name] = version
        if name in locked_versions:
            if version not in locked_versions[name]:
                errors.append(f"{name}: export {version} differs from uv.lock {sorted(locked_versions[name])}")
        elif name not in declared:
            errors.append(f"{name}: absent from both uv.lock and pyproject.toml")
        if name in declared and not declared[name].specifier.contains(version, prereleases=True):
            errors.append(f"{name}: export {version} violates pyproject.toml {declared[name].specifier}")

    for name in declared:
        if name in selected or name in EXCLUDED_ROOTS:
            continue
        # Local workspace packages never reach the generated file; their path
        # line stands in for a pinned requirement.
        path = local_sources.get(name)
        if path is not None and path in included_local:
            continue
        errors.append(f"{name}: missing export dependency; regenerate or explicitly classify as runtime-only")
    for name, path in local_sources.items():
        if path not in included_local:
            errors.append(f"{name}: local source {path} missing from requirements-openapi.txt")
    if errors:
        raise ValueError("OpenAPI dependency drift:\n" + "\n".join(errors))
    return len(selected)


if __name__ == "__main__":
    count = validate(Path(__file__).resolve().parents[1])
    print(f"Validated {count} pinned OpenAPI dependencies against this checkout")
