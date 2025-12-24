# -*- coding: utf-8 -*-
"""Memory Configuration Model - Backward Compatibility

This module provides backward compatibility for imports.
All classes have been moved to app.schemas.memory_config_schema.

DEPRECATED: Import from app.schemas.memory_config_schema instead.
"""

# Re-export for backward compatibility
from app.schemas.memory_config_schema import (
    ConfigurationError,
    InvalidConfigError,
    MemoryConfig,
    MemoryConfigValidation,
    ModelInactiveError,
    ModelNotFoundError,
    ModelValidation,
    WorkspaceNotFoundError,
    WorkspaceValidation,
    validate_memory_config_data,
    validate_model_data,
    validate_workspace_data,
)

__all__ = [
    "ConfigurationError",
    "InvalidConfigError",
    "MemoryConfig",
    "MemoryConfigValidation",
    "ModelInactiveError",
    "ModelNotFoundError",
    "ModelValidation",
    "WorkspaceNotFoundError",
    "WorkspaceValidation",
    "validate_memory_config_data",
    "validate_model_data",
    "validate_workspace_data",
]
