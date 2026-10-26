from __future__ import annotations

import importlib
import subprocess
import sys

import pytest

from src.backends import FakeBackend
from src.backends.factory import BACKEND_ENV, KNOWN_BACKENDS, available_backends, build_backend
from src.clock import ManualClock
from src.envconf import ConfigError

CUBE_ENV = {
    BACKEND_ENV: "cube",
    "SANDBOX_CUBE_API_URL": "http://cube-api:3000",
    "SANDBOX_CUBE_PROXY_ADDR": "cube-proxy:8080",
}


def test_unset_backend_is_a_config_error_naming_the_variable():
    with pytest.raises(ConfigError, match=BACKEND_ENV):
        build_backend({}, ManualClock())


def test_blank_backend_counts_as_unset():
    with pytest.raises(ConfigError, match=BACKEND_ENV):
        build_backend({BACKEND_ENV: "   "}, ManualClock())


def test_unknown_backend_lists_the_choices():
    with pytest.raises(ConfigError) as excinfo:
        build_backend({BACKEND_ENV: "nope"}, ManualClock())
    message = str(excinfo.value)
    assert "nope" in message
    assert all(name in message for name in KNOWN_BACKENDS)


def test_fake_backend_is_built():
    backend = build_backend({BACKEND_ENV: "fake"}, ManualClock())
    assert isinstance(backend, FakeBackend)


def test_cube_backend_is_built_from_its_own_variables():
    backend = build_backend(CUBE_ENV, ManualClock())
    assert backend.name == "cube"


@pytest.mark.parametrize("missing", ["SANDBOX_CUBE_API_URL", "SANDBOX_CUBE_PROXY_ADDR"])
def test_cube_required_variables_are_named_in_the_error(missing):
    env = {k: v for k, v in CUBE_ENV.items() if k != missing}
    with pytest.raises(ConfigError, match=missing):
        build_backend(env, ManualClock())


def test_cube_value_errors_surface_as_config_errors():
    env = {**CUBE_ENV, "SANDBOX_CUBE_ROUTE_MODE": "sideways"}
    with pytest.raises(ConfigError):
        build_backend(env, ManualClock())


def test_available_backends_always_include_fake():
    names = available_backends()
    assert "fake" in names
    assert set(names) <= set(KNOWN_BACKENDS)


def test_missing_backend_package_is_reported_as_not_in_this_build(monkeypatch):
    def fake_import(name, *args, **kwargs):
        raise ModuleNotFoundError(f"No module named {name!r}", name=name)

    monkeypatch.setattr(importlib, "import_module", fake_import)
    with pytest.raises(ConfigError, match="本构建不含"):
        build_backend({BACKEND_ENV: "cube"}, ManualClock())


def test_missing_third_party_dependency_points_at_the_extra(monkeypatch):
    def fake_import(name, *args, **kwargs):
        raise ModuleNotFoundError("No module named 'httpx'", name="httpx")

    monkeypatch.setattr(importlib, "import_module", fake_import)
    with pytest.raises(ConfigError, match="httpx") as excinfo:
        build_backend({BACKEND_ENV: "cube"}, ManualClock())
    assert "--extra cube" in str(excinfo.value)


def test_selecting_fake_never_imports_a_real_backend_package():
    # Fresh interpreter: other tests in this process have already imported the cube package.
    code = (
        "import sys\n"
        "from src.backends.factory import build_backend\n"
        "from src.clock import Clock\n"
        "build_backend({'SANDBOX_BACKEND': 'fake'}, Clock())\n"
        "loaded = [m for m in sys.modules if m.startswith('src.backends.') "
        "and m.split('.')[2] not in ('base', 'fake', 'factory')]\n"
        "assert not loaded, loaded\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
