"""Finite service errors, safe parameters, and legacy wire semantics."""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Literal

logger = logging.getLogger(__name__)
ErrorResponseStyle = Literal["http", "business", "internal"]
type PublicScalar = str | int | float | bool
MAX_PUBLIC_PARAM_LENGTH = 200


@dataclass(frozen=True)
class ErrorDefinition:
    """One cause's wire contract and allowed public parameter types."""

    status_code: int
    retryable: bool
    response_code: int
    response_style: ErrorResponseStyle
    params: Mapping[str, type] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        object.__setattr__(self, "params", MappingProxyType(dict(self.params)))


_DEFINITIONS = {
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
    "KB_MULTIMODAL_INPUT_LIMIT": ErrorDefinition(400, False, 400, "http"),
    "KB_MULTIMODAL_EMBEDDING_FAILED": ErrorDefinition(502, True, 10001, "internal"),
    "KB_MULTIMODAL_RERANK_FAILED": ErrorDefinition(502, True, 10001, "internal"),
    "KB_DATABASE_UNAVAILABLE": ErrorDefinition(500, True, 10001, "internal"),
    "KB_INTERNAL_ERROR": ErrorDefinition(500, False, 10001, "internal"),
    "KB_HTTP_UNAUTHORIZED": ErrorDefinition(401, False, 401, "http"),
    "KB_HTTP_FORBIDDEN": ErrorDefinition(403, False, 403, "http"),
    "KB_HTTP_METHOD_NOT_ALLOWED": ErrorDefinition(405, False, 405, "http"),
    "KB_HTTP_VALIDATION_ERROR": ErrorDefinition(422, False, 422, "http"),
    "KB_HTTP_TOO_LARGE": ErrorDefinition(413, False, 413, "http"),
    "KB_HTTP_RATE_LIMITED": ErrorDefinition(429, False, 429, "http"),
    "KB_HTTP_BAD_GATEWAY": ErrorDefinition(502, False, 502, "http"),
    "KB_HTTP_UNAVAILABLE": ErrorDefinition(503, False, 503, "http"),
    "KB_HTTP_TIMEOUT": ErrorDefinition(504, False, 504, "http"),
    "KB_RETRIEVAL_REQUEST_INVALID": ErrorDefinition(400, False, 400, "http"),
    "KB_RERANK_CONFIG_INVALID": ErrorDefinition(400, False, 400, "http"),
}

ERROR_DEFINITIONS: Mapping[str, ErrorDefinition] = MappingProxyType(_DEFINITIONS)
ErrorCode = StrEnum("ErrorCode", {key: key for key in ERROR_DEFINITIONS})


def valid_params(code: str, params: Mapping[str, PublicScalar]) -> bool:
    definition = ERROR_DEFINITIONS.get(code)
    if definition is None or params.keys() != definition.params.keys():
        return False
    for key, expected_type in definition.params.items():
        value = params[key]
        if type(value) is not expected_type:
            return False
        if isinstance(value, str) and (
            len(value) > MAX_PUBLIC_PARAM_LENGTH or any(ord(char) < 32 for char in value)
        ):
            return False
        if isinstance(value, float) and not math.isfinite(value):
            return False
    return True


class KnowledgeError(Exception):
    """Untranslated cause and safe parameters; old task constructors stay compatible."""

    def __init__(
        self,
        *,
        code: str,
        message: str | None = None,
        params: Mapping[str, PublicScalar] | None = None,
        status_code: int | None = None,
        retryable: bool | None = None,
        response_code: int | None = None,
        response_style: ErrorResponseStyle | None = None,
    ) -> None:
        # Never retain a provider message as public text or exception string.
        supplied = dict(params or {})
        if not valid_params(code, supplied):
            logger.error("Knowledge error definition invalid")
            code, supplied = "KB_INTERNAL_ERROR", {}
            status_code = response_code = response_style = retryable = None
        definition = ERROR_DEFINITIONS[code]
        super().__init__(str(code))
        self.code = str(code)
        self.params = MappingProxyType(supplied)
        self.message = str(code)  # Source compatibility for excluded task consumers.
        self.status_code = definition.status_code if status_code is None else status_code
        self.retryable = definition.retryable if retryable is None else retryable
        self.response_code = definition.response_code if response_code is None else response_code
        self.response_style = (
            definition.response_style if response_style is None else response_style
        )

    @classmethod
    def from_code(
        cls,
        code: str,
        message: str | None = None,
        *,
        params: Mapping[str, PublicScalar] | None = None,
        status_code: int | None = None,
        response_code: int | None = None,
        response_style: ErrorResponseStyle | None = None,
    ) -> KnowledgeError:
        return cls(
            code=code,
            message=message,
            params=params,
            status_code=status_code,
            response_code=response_code,
            response_style=response_style,
        )
