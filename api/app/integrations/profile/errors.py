"""Transport-neutral errors for the independent memory profile service."""

from __future__ import annotations


class MemoryProfileClientError(Exception):
    """Base class for memory profile integration failures."""


class MemoryProfileServiceError(MemoryProfileClientError):
    """The profile service returned an unsuccessful HTTP status."""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


class MemoryProfileUnavailableError(MemoryProfileClientError):
    """The profile service could not be reached."""


class MemoryProfileTimeoutError(MemoryProfileClientError):
    """The profile service did not respond before the configured timeout."""


class MemoryProfileProtocolError(MemoryProfileClientError):
    """The profile service response did not match the internal JSON contract."""


class MemoryProfileConfigurationError(MemoryProfileClientError, ValueError):
    """The profile service integration configuration is invalid."""
