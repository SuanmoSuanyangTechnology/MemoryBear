"""Service-local error classification with legacy wire compatibility."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ErrorResponseStyle = Literal["http", "business", "internal"]


@dataclass(frozen=True)
class ErrorDefinition:
    """Default transport semantics for one internal error classification."""

    status_code: int
    retryable: bool
    response_code: int
    response_style: ErrorResponseStyle


ERROR_DEFINITIONS = {
    "KB_PRINCIPAL_INVALID": ErrorDefinition(400, False, 400, "http"),
    "KB_VALIDATION_ERROR": ErrorDefinition(400, False, 400, "http"),
    "KB_RESOURCE_NOT_FOUND": ErrorDefinition(404, False, 404, "http"),
    "KB_CONFLICT": ErrorDefinition(409, False, 409, "http"),
    "KB_REFERENCE_NOT_FOUND": ErrorDefinition(500, False, 10001, "internal"),
    "KB_METADATA_TYPE_MISMATCH": ErrorDefinition(400, False, 1001, "business"),
    "KB_TASK_DISPATCH_FAILED": ErrorDefinition(500, True, 10001, "internal"),
    "KB_STORAGE_UNAVAILABLE": ErrorDefinition(500, True, 10001, "internal"),
    "KB_SEARCH_UNAVAILABLE": ErrorDefinition(500, True, 10001, "internal"),
    "KB_MODEL_UNAVAILABLE": ErrorDefinition(400, False, 400, "http"),
    "KB_DATABASE_UNAVAILABLE": ErrorDefinition(500, True, 10001, "internal"),
    "KB_INTERNAL_ERROR": ErrorDefinition(500, False, 10001, "internal"),
}


class KnowledgeError(Exception):
    """Internal classification separated from the legacy response contract."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        status_code: int,
        retryable: bool,
        response_code: int,
        response_style: ErrorResponseStyle,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
        self.response_code = response_code
        self.response_style = response_style

    @classmethod
    def from_code(
        cls,
        code: str,
        message: str,
        *,
        status_code: int | None = None,
        response_code: int | None = None,
        response_style: ErrorResponseStyle | None = None,
    ) -> KnowledgeError:
        """Construct an error and optionally preserve an operation-specific wire code."""

        definition = ERROR_DEFINITIONS[code]
        return cls(
            code=code,
            message=message,
            status_code=(status_code if status_code is not None else definition.status_code),
            retryable=definition.retryable,
            response_code=(
                response_code if response_code is not None else definition.response_code
            ),
            response_style=response_style or definition.response_style,
        )


__all__ = [
    "ERROR_DEFINITIONS",
    "ErrorDefinition",
    "ErrorResponseStyle",
    "KnowledgeError",
]
