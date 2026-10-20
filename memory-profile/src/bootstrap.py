"""Deterministic settings bootstrap independent of the process cwd."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import dotenv_values

from src.config import ProfileSettings

_SERVICE_ROOT = Path(__file__).resolve().parents[1]
_REPOSITORY_ROOT = _SERVICE_ROOT.parent


@dataclass(frozen=True)
class BootstrapPaths:
    repository_root: Path
    service_root: Path
    root_env_file: Path
    service_env_file: Path


def _absolute(value: str | Path) -> Path:
    return Path(value).expanduser().resolve()


def resolve_bootstrap_paths(environ: Mapping[str, str] | None = None) -> BootstrapPaths:
    process_env = dict(os.environ if environ is None else environ)
    repository_root = _absolute(process_env.get("MEMORYBEAR_ROOT_DIR", _REPOSITORY_ROOT))
    service_root = _absolute(
        process_env.get("PROFILE_SERVICE_DIR", repository_root / "memory-profile")
    )
    explicit_root = process_env.get("MEMORYBEAR_ROOT_ENV_FILE")
    explicit_service = process_env.get("PROFILE_ENV_FILE")
    root_env = _absolute(explicit_root or repository_root / ".env")
    service_env = _absolute(explicit_service or service_root / ".env")
    if explicit_root and not root_env.is_file():
        raise FileNotFoundError(f"Explicit root env file does not exist: {root_env}")
    if explicit_service and not service_env.is_file():
        raise FileNotFoundError(f"Explicit profile env file does not exist: {service_env}")
    return BootstrapPaths(repository_root, service_root, root_env, service_env)


def _read_env(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    return {key: value for key, value in dotenv_values(path).items() if value is not None}


def load_settings(
    paths: BootstrapPaths | None = None,
    environ: Mapping[str, str] | None = None,
) -> ProfileSettings:
    process_env = dict(os.environ if environ is None else environ)
    resolved = paths or resolve_bootstrap_paths(process_env)
    merged: dict[str, str] = {}
    merged.update(_read_env(resolved.root_env_file))
    merged.update(_read_env(resolved.service_env_file))
    merged.update(process_env)
    return ProfileSettings.model_validate(merged)


@lru_cache(maxsize=1)
def get_settings() -> ProfileSettings:
    return load_settings()


def clear_settings_cache() -> None:
    get_settings.cache_clear()
