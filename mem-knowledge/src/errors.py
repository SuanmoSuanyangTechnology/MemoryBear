"""Service-local errors independent of API BizCode."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ErrorDefinition:
    """Stable HTTP and retry semantics for one internal error code."""

    status_code: int
    retryable: bool


ERROR_DEFINITIONS = {
    "KB_PRINCIPAL_INVALID": ErrorDefinition(400, False),
    "KB_VALIDATION_ERROR": ErrorDefinition(400, False),
    "KB_RESOURCE_NOT_FOUND": ErrorDefinition(404, False),
    "KB_CONFLICT": ErrorDefinition(409, False),
    "KB_REFERENCE_NOT_FOUND": ErrorDefinition(409, False),
    "KB_METADATA_TYPE_MISMATCH": ErrorDefinition(422, False),
    "KB_TASK_DISPATCH_FAILED": ErrorDefinition(503, True),
    "KB_STORAGE_UNAVAILABLE": ErrorDefinition(503, True),
    "KB_SEARCH_UNAVAILABLE": ErrorDefinition(503, True),
    "KB_MODEL_UNAVAILABLE": ErrorDefinition(503, True),
    "KB_DATABASE_UNAVAILABLE": ErrorDefinition(503, True),
    "KB_INTERNAL_ERROR": ErrorDefinition(500, False),
}


class KnowledgeError(Exception):
    """Stable internal error with HTTP and retry semantics."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        status_code: int,
        retryable: bool,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable

    @classmethod
    def from_code(cls, code: str, message: str) -> KnowledgeError:
        """Construct an error from the fixed internal definition table."""

        definition = ERROR_DEFINITIONS[code]
        return cls(
            code=code,
            message=message,
            status_code=definition.status_code,
            retryable=definition.retryable,
        )


__all__ = ["ERROR_DEFINITIONS", "ErrorDefinition", "KnowledgeError"]
