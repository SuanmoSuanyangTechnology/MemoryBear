"""Structured service errors.

The numeric codes are a draft: the new service's contract is not frozen yet.
Quota / rate-limit reuse the main application's 3018 / 3019.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# kind -> (numeric code, HTTP status)
ERROR_TABLE: dict[str, tuple[int, int]] = {
    "invalid_request": (11001, 422),
    "unauthorized": (11002, 401),
    "backend_unavailable": (11005, 503),
    "sandbox_busy": (11010, 503),
    "instance_lost": (11011, 503),
    "kernel_lost": (11012, 503),
    "path_forbidden": (11020, 403),
    "file_not_found": (11021, 404),
    "file_exists": (11022, 409),
    "artifact_gone": (11023, 410),
    "file_too_large": (11024, 413),
    "quota_exceeded": (3018, 402),
    "rate_limited": (3019, 429),
    "internal_error": (11099, 500),
}


@dataclass
class SandboxError(Exception):
    """A request the service refused or could not serve."""

    kind: str
    message: str
    retryable: bool = False
    retry_after_s: float | None = None
    params: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in ERROR_TABLE:
            raise ValueError(f"unknown sandbox error kind: {self.kind}")
        super().__init__(f"{self.kind}: {self.message}")

    @property
    def code(self) -> int:
        return ERROR_TABLE[self.kind][0]

    @property
    def status_code(self) -> int:
        return ERROR_TABLE[self.kind][1]


def invalid_request(message: str, **params: Any) -> SandboxError:
    return SandboxError("invalid_request", message, params=params)
