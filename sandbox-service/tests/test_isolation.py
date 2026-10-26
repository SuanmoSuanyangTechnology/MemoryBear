"""Isolation rules between backend packages and the shared layer.

R1  a backend package imports only the base contract, the clock, envconf, the
    standard library, its own modules and its declared third-party packages
R2  backend packages never name each other (whole word, source and comments)
R3  shared code (everything except backend packages and ``factory.py``) never
    names a real backend
R6  test files under ``tests/backends/<name>/`` carry the backend name
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
BACKENDS = SRC / "backends"
TESTS_BACKENDS = ROOT / "tests" / "backends"

# Backend package name -> third-party packages it may import.
THIRD_PARTY = {"cube": {"httpx"}, "e2b": set()}
SHARED_ALLOWED = {"src.backends.base", "src.clock", "src.envconf"}
KNOWN_NAMES = tuple(THIRD_PARTY)


def present_backends() -> list[str]:
    return [n for n in THIRD_PARTY if (BACKENDS / n).is_dir()]


def py_files(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob("*.py") if "__pycache__" not in p.parts)


def imported_modules(path: Path, package: str) -> set[str]:
    """Absolute module names imported by ``path`` (relative imports resolved)."""

    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                found.add(node.module or "")
                continue
            base = package.split(".")
            base = base[: len(base) - (node.level - 1)]
            found.add(".".join([*base, *([node.module] if node.module else [])]))
    return found


def mentions(path: Path, name: str, *, whole_word: bool) -> bool:
    pattern = rf"\b{name}\b" if whole_word else name
    return re.search(pattern, path.read_text(encoding="utf-8"), re.IGNORECASE) is not None


def test_at_least_one_real_backend_package_is_checked():
    assert present_backends(), "no backend package found; the isolation checks would be vacuous"


@pytest.mark.parametrize("name", present_backends())
def test_r1_backend_imports_stay_inside_the_allowed_set(name):
    package = f"src.backends.{name}"
    own = package
    for path in py_files(BACKENDS / name):
        for module in imported_modules(path, package):
            top = module.split(".")[0]
            if module == own or module.startswith(own + "."):
                continue
            if module in SHARED_ALLOWED:
                continue
            if top in sys.stdlib_module_names or top in THIRD_PARTY[name]:
                continue
            pytest.fail(f"{path.relative_to(ROOT)} imports {module!r}")


@pytest.mark.parametrize("name", present_backends())
def test_r2_backends_do_not_name_each_other(name):
    others = [n for n in KNOWN_NAMES if n != name]
    for path in py_files(BACKENDS / name):
        for other in others:
            assert not mentions(path, other, whole_word=True), (
                f"{path.relative_to(ROOT)} mentions {other}"
            )


def test_r3_shared_code_does_not_name_a_real_backend():
    skipped = {BACKENDS / n for n in KNOWN_NAMES} | {BACKENDS / "factory.py"}
    for path in py_files(SRC):
        if any(path == s or s in path.parents for s in skipped):
            continue
        for name in KNOWN_NAMES:
            assert not mentions(path, name, whole_word=False), (
                f"{path.relative_to(ROOT)} mentions {name}"
            )


@pytest.mark.parametrize("name", [n for n in THIRD_PARTY if (TESTS_BACKENDS / n).is_dir()])
def test_r6_backend_tests_carry_the_backend_name(name):
    for path in py_files(TESTS_BACKENDS / name):
        if path.name in {"conftest.py", "__init__.py"}:
            continue
        assert name in path.name, f"{path.relative_to(ROOT)} should contain {name!r}"
