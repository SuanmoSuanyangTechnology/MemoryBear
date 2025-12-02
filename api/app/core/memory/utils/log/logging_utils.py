"""Logging utilities for prompt rendering and timing.

This module provides backward-compatible access to memory module logging utilities
that have been unified into the centralized logging system (app.core.logging_config).

All logging functions are now imported from the centralized configuration to ensure
consistent behavior, formatting, and configuration across the entire application.

For new code, consider importing directly from app.core.logging_config:
    from app.core.logging_config import log_prompt_rendering, log_template_rendering, log_time

This module maintains backward compatibility for existing code that imports from here.
"""

# Import from centralized logging configuration
from app.core.logging_config import (
    log_prompt_rendering as _log_prompt_rendering,
    log_template_rendering as _log_template_rendering,
    log_time as _log_time,
    get_prompt_logger as _get_prompt_logger,
)

# Re-export functions to maintain backward compatibility
log_prompt_rendering = _log_prompt_rendering
log_template_rendering = _log_template_rendering
log_time = _log_time

# Re-export prompt_logger for backward compatibility with code that uses it directly
# This provides the same logger instance that was previously created in this module
prompt_logger = _get_prompt_logger()

# Expose functions in __all__ for explicit exports
__all__ = [
    'log_prompt_rendering',
    'log_template_rendering',
    'log_time',
    'prompt_logger',
]
