"""Transport-neutral sandbox service client errors."""

from __future__ import annotations


class SandboxClientError(Exception):
    """Base class for sandbox integration failures."""


class SandboxConfigurationError(SandboxClientError):
    """The integration is disabled or its settings are invalid."""


class SandboxServiceError(SandboxClientError):
    """The sandbox service refused the request with an explicit business error."""

    def __init__(
        self,
        *,
        status_code: int,
        code: int,
        kind: str,
        message: str,
        retryable: bool = False,
        retry_after_s: float | None = None,
    ):
        super().__init__(f"{kind}: {message}")
        self.status_code = status_code
        self.code = code
        self.kind = kind
        self.message = message
        self.retryable = retryable
        self.retry_after_s = retry_after_s


class SandboxUnavailableError(SandboxClientError):
    """The sandbox service could not be reached."""


class SandboxTimeoutError(SandboxClientError):
    """The sandbox service did not answer before the configured timeout."""


class SandboxProtocolError(SandboxClientError):
    """The sandbox service response did not match the internal contract."""
