"""Deterministic environment loading for the knowledge service."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import dotenv_values

from .config import KnowledgeSettings

_SOURCE_SERVICE_ROOT = Path(__file__).resolve().parents[2]
_SOURCE_REPOSITORY_ROOT = _SOURCE_SERVICE_ROOT.parent


@dataclass(frozen=True)
class BootstrapPaths:
    """Absolute repository and environment-file locations."""

    repository_root: Path
    service_root: Path
    root_env_file: Path
    service_env_file: Path


def _absolute_path(value: str | Path) -> Path:
    return Path(value).expanduser().resolve()


def resolve_bootstrap_paths(
    environ: Mapping[str, str] | None = None,
) -> BootstrapPaths:
    """Resolve bootstrap paths without depending on the process cwd."""

    process_env = dict(os.environ if environ is None else environ)
    repository_root = _absolute_path(
        process_env.get("MEMORYBEAR_ROOT_DIR", _SOURCE_REPOSITORY_ROOT)
    )
    service_root = _absolute_path(
        process_env.get(
            "MEM_KNOWLEDGE_SERVICE_DIR",
            repository_root / "mem-knowledge",
        )
    )

    explicit_root_env = process_env.get("MEMORYBEAR_ROOT_ENV_FILE")
    explicit_service_env = process_env.get("MEM_KNOWLEDGE_ENV_FILE")
    root_env_file = _absolute_path(explicit_root_env or repository_root / ".env")
    service_env_file = _absolute_path(explicit_service_env or service_root / ".env")

    if explicit_root_env and not root_env_file.is_file():
        raise FileNotFoundError(f"Explicit root env file does not exist: {root_env_file}")
    if explicit_service_env and not service_env_file.is_file():
        raise FileNotFoundError(
            f"Explicit knowledge env file does not exist: {service_env_file}"
        )

    return BootstrapPaths(
        repository_root=repository_root,
        service_root=service_root,
        root_env_file=root_env_file,
        service_env_file=service_env_file,
    )


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    return {
        key: value
        for key, value in dotenv_values(path).items()
        if value is not None
    }


def load_settings(
    paths: BootstrapPaths | None = None,
    environ: Mapping[str, str] | None = None,
) -> KnowledgeSettings:
    """Merge defaults, root env, service env, and runtime env exactly once."""

    process_env = dict(os.environ if environ is None else environ)
    resolved_paths = paths or resolve_bootstrap_paths(process_env)
    merged: dict[str, str] = {}
    merged.update(_read_env_file(resolved_paths.root_env_file))
    merged.update(_read_env_file(resolved_paths.service_env_file))
    merged.update(process_env)
    return KnowledgeSettings(**merged)


@lru_cache(maxsize=1)
def get_settings() -> KnowledgeSettings:
    """Return the process-level immutable settings object."""

    return load_settings()


def clear_settings_cache() -> None:
    """Clear the process settings cache for tests and fork resets."""

    get_settings.cache_clear()
