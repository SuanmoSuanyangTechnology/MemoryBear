"""Backend selection: the only module that knows backend names.

``SANDBOX_BACKEND`` picks one backend package. Only the selected package is
imported, so a build without the other packages still starts. Every problem is
raised as ``ConfigError`` at process start, never on the first request.
"""

from __future__ import annotations

import importlib
import importlib.util
from collections.abc import Mapping

from ..clock import Clock
from ..envconf import ConfigError
from .base import SandboxBackend

BACKEND_ENV = "SANDBOX_BACKEND"
KNOWN_BACKENDS = ("cube", "fake")


def available_backends() -> list[str]:
    """Backend names whose package is present in this build."""

    names: list[str] = []
    for name in KNOWN_BACKENDS:
        if name == "fake" or importlib.util.find_spec(f"{__package__}.{name}") is not None:
            names.append(name)
    return names


def _choices(names: list[str] | tuple[str, ...]) -> str:
    return "、".join(names)


def build_backend(env: Mapping[str, str], clock: Clock) -> SandboxBackend:
    raw = (env.get(BACKEND_ENV) or "").strip()
    if not raw:
        raise ConfigError(f"{BACKEND_ENV} 未设置，可选值：{_choices(KNOWN_BACKENDS)}")
    if raw not in KNOWN_BACKENDS:
        raise ConfigError(f"{BACKEND_ENV}={raw} 不是已知后端，可选值：{_choices(KNOWN_BACKENDS)}")

    if raw == "fake":
        from .fake import FakeBackend

        return FakeBackend(clock)

    module_name = f"{__package__}.{raw}"
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name:
            raise ConfigError(
                f"本构建不含 {raw} 后端，可用：{_choices(available_backends())}"
            ) from exc
        raise ConfigError(
            f"{raw} 后端的依赖未安装（缺少 {exc.name}），请 uv sync --extra {raw}"
        ) from exc
    return module.build(env, clock)
