"""
Agent logger module for backward compatibility.

This module maintains the get_named_logger() function for backward compatibility
while delegating to the centralized logging configuration.

All new code should import directly from app.core.logging_config instead.
"""

__version__ = "0.1.0"
__author__ = "RED_BEAR"

from app.core.logging_config import get_agent_logger


def get_named_logger(name):
    """Get a named logger for agent operations.

    This function maintains backward compatibility with existing code.
    It delegates to the centralized get_agent_logger() function.

    Args:
        name: Logger name for namespacing

    Returns:
        Logger configured for agent operations

    Example:
        >>> logger = get_named_logger("my_agent")
        >>> logger.info("Agent operation started")
    """
    return get_agent_logger(name)
