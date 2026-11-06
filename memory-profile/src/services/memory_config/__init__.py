"""Memory-configuration services that operate on the ``memory_config`` table.

Reads (:mod:`query`) and writes (:mod:`mutation`), with ownership resolution in
:mod:`ownership`. Trial-run work — model probes, static previews and the scene
demo — lives in :mod:`src.services.memory_config_trial` and never touches these
tables.
"""

from src.services.memory_config.errors import (
    ConfigAccessDeniedError,
    ConfigNotFoundError,
    ConfigQueryError,
    ConfigValidationError,
    DuplicateConfigNameError,
    InvalidConfigIdError,
    NoFieldsToUpdateError,
    WorkspaceNotFoundError,
)
from src.services.memory_config.mutation import MemoryConfigMutationService
from src.services.memory_config.query import MemoryConfigQueryService

__all__ = [
    "ConfigAccessDeniedError",
    "ConfigNotFoundError",
    "ConfigQueryError",
    "ConfigValidationError",
    "DuplicateConfigNameError",
    "InvalidConfigIdError",
    "MemoryConfigMutationService",
    "MemoryConfigQueryService",
    "NoFieldsToUpdateError",
    "WorkspaceNotFoundError",
]
