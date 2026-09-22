"""Transport-neutral model service client errors."""

from __future__ import annotations


class ModelServiceClientError(Exception):
    """Base class for model service integration failures."""


class ModelServiceUnavailableError(ModelServiceClientError):
    """The model service could not be reached."""


class ModelServiceTimeoutError(ModelServiceClientError):
    """The model service did not respond before the configured timeout."""


class ModelServiceConfigurationError(ModelServiceClientError):
    """The model service integration configuration is invalid."""
