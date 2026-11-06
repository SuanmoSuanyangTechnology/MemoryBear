"""Typed failures of the memory-configuration module.

Each class names one legacy outcome the HTTP layer must reproduce, so handlers
never have to inspect free-form messages to choose an error code:

- :class:`InvalidConfigIdError` -> ``code=1003``
- :class:`ConfigNotFoundError` / :class:`ConfigAccessDeniedError` -> ``code=9503``
- :class:`ConfigQueryError` -> ``code=10001`` or an HTTP 500
- :class:`ConfigValidationError` -> an HTTP 500 (invalid stored configuration)
"""

from __future__ import annotations


class InvalidConfigIdError(ValueError):
    """The id is neither a UUID nor a resolvable legacy integer id."""


class ConfigNotFoundError(LookupError):
    """No configuration exists for the resolved id."""


class ConfigAccessDeniedError(LookupError):
    """The configuration exists but belongs to another workspace."""


class ConfigQueryError(RuntimeError):
    """The stored row cannot be serialized (zero divisor, unreadable values…)."""


class ConfigValidationError(ValueError):
    """The stored row violates the legacy response model's constraints."""


class DuplicateConfigNameError(ValueError):
    """A configuration with the same name already exists in the workspace."""

    def __init__(self, config_name: str) -> None:
        super().__init__(config_name)
        self.config_name = config_name


class NoFieldsToUpdateError(ValueError):
    """The update body carries none of the fields the endpoint accepts."""


class WorkspaceNotFoundError(LookupError):
    """The workspace is missing or inactive (legacy ``workspace.not_found``)."""
