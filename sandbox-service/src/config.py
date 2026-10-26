"""Service configuration loaded from ``SANDBOX_*`` environment variables.

Each tunable of ``SchedulerConfig`` and ``OutputBudget`` maps to one variable named
``SANDBOX_<FIELD_NAME_UPPERCASE>`` (for example ``SANDBOX_CAPACITY`` or
``SANDBOX_P_MIN_S``). Unset variables keep the dataclass default. Parsing follows
the field type, so adding a field to either dataclass needs no change here.

Output budget variables use the ``SANDBOX_`` prefix as well:
``SANDBOX_STDOUT_MAX_BYTES``, ``SANDBOX_STDERR_MAX_BYTES``, ``SANDBOX_HEAD_RATIO``.

The parsing itself lives in ``envconf`` so backend packages can reuse it.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from . import envconf
from .envconf import ConfigError
from .files.config import FilesConfig
from .services.scheduler_config import SchedulerConfig
from .services.verdict import OutputBudget

ENV_PREFIX = "SANDBOX_"

__all__ = ["ENV_PREFIX", "ConfigError", "ServiceConfig", "known_variables", "load_config"]


@dataclass(frozen=True)
class ServiceConfig:
    scheduler: SchedulerConfig
    budget: OutputBudget
    files: FilesConfig


def known_variables() -> list[str]:
    """Every environment variable this module reads, sorted."""

    return envconf.known_variables((SchedulerConfig, OutputBudget, FilesConfig), ENV_PREFIX)


def load_config(env: Mapping[str, str] | None = None) -> ServiceConfig:
    """Build the service configuration from ``env`` (default: ``os.environ``)."""

    source = os.environ if env is None else env
    return ServiceConfig(
        scheduler=envconf.build(SchedulerConfig, source, ENV_PREFIX),
        budget=envconf.build(OutputBudget, source, ENV_PREFIX),
        files=envconf.build(FilesConfig, source, ENV_PREFIX),
    )
