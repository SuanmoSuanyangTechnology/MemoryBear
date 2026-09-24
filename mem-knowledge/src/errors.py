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


def public_text(value: str) -> str:
    """Bound an already-authorized display label without changing the error contract."""
    return "".join(
        " " if ord(char) < 32 or ord(char) == 127 else char
        for char in value[:MAX_PUBLIC_PARAM_LENGTH]
    )


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


# Business resource absence uses HTTP 400 so legacy clients do not mistake it
# for a missing API route. Keep numeric code 404 for existing consumers.
_DEFINITIONS = {
    "KB_PRINCIPAL_INVALID": ErrorDefinition(400, False, 400, "http"),
    "KB_VALIDATION_ERROR": ErrorDefinition(400, False, 400, "http"),
    "KB_RESOURCE_NOT_FOUND": ErrorDefinition(400, False, 404, "business"),
    "KB_CONFLICT": ErrorDefinition(409, False, 409, "http"),
    "KB_REFERENCE_NOT_FOUND": ErrorDefinition(400, False, 10001, "business"),
    "KB_METADATA_TYPE_MISMATCH": ErrorDefinition(400, False, 1001, "business"),
    "KB_TASK_DISPATCH_FAILED": ErrorDefinition(400, True, 10001, "business"),
    "KB_STORAGE_UNAVAILABLE": ErrorDefinition(400, True, 10001, "business"),
    "KB_SEARCH_UNAVAILABLE": ErrorDefinition(400, True, 10001, "business"),
    "KB_EMBEDDING_TIMEOUT": ErrorDefinition(400, True, 10001, "business"),
    "KB_EMBEDDING_CONNECTION_FAILED": ErrorDefinition(400, True, 10001, "business"),
    "KB_EMBEDDING_RATE_LIMITED": ErrorDefinition(400, True, 10001, "business"),
    "KB_EMBEDDING_REQUEST_FAILED": ErrorDefinition(400, False, 10001, "business"),
    "KB_EMBEDDING_SERVICE_UNAVAILABLE": ErrorDefinition(400, True, 10001, "business"),
    "KB_MODEL_UNAVAILABLE": ErrorDefinition(400, False, 400, "http"),
    "KB_MULTIMODAL_INPUT_LIMIT": ErrorDefinition(400, False, 400, "http"),
    "KB_MULTIMODAL_EMBEDDING_FAILED": ErrorDefinition(400, True, 10001, "business"),
    "KB_MULTIMODAL_RERANK_FAILED": ErrorDefinition(400, True, 10001, "business"),
    "KB_DATABASE_UNAVAILABLE": ErrorDefinition(400, True, 10001, "business"),
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
    "KB_BUILTIN_METADATA_FIELD_UNKNOWN": ErrorDefinition(400, False, 400, "http"),
    "KB_BUILTIN_METADATA_OPERATOR_UNSUPPORTED": ErrorDefinition(400, False, 400, "http"),
    "KB_CHILD_PARENT_ID_REQUIRED": ErrorDefinition(400, False, 400, "http"),
    "KB_CHUNK_BATCH_LIMIT": ErrorDefinition(400, False, 400, "http", {"max_count": int}),
    "KB_CHUNK_NOT_FOUND": ErrorDefinition(400, False, 404, "business"),
    "KB_DOCUMENT_CHUNK_MODE_CHANGE_FORBIDDEN": ErrorDefinition(400, False, 400, "http"),
    "KB_DOCUMENT_NOT_FOUND": ErrorDefinition(400, False, 404, "business"),
    "KB_DOCUMENT_PARSER_CONFIG_INVALID": ErrorDefinition(400, False, 400, "http"),
    "KB_EMBEDDING_MODEL_UNAVAILABLE": ErrorDefinition(400, False, 400, "http"),
    "KB_EXTERNAL_ID_EXISTS": ErrorDefinition(400, False, 1001, "business"),
    "KB_FEISHU_AUTH_INVALID": ErrorDefinition(200, False, 2001, "business"),
    "KB_FILE_NOT_FOUND": ErrorDefinition(400, False, 404, "business"),
    "KB_FILE_STORAGE_KEY_MISSING": ErrorDefinition(400, False, 404, "business"),
    "KB_FILTER_LOGIC_INVALID": ErrorDefinition(400, False, 400, "http"),
    "KB_GRAPH_CONFIG_INVALID": ErrorDefinition(400, False, 400, "http"),
    "KB_GRAPH_ENTITY_TYPES_UNAVAILABLE": ErrorDefinition(400, False, 400, "http"),
    "KB_GRAPH_NOT_ENABLED": ErrorDefinition(400, False, 400, "http"),
    "KB_IMAGE_BASE64_INVALID": ErrorDefinition(400, False, 400, "http"),
    "KB_IMAGE_CONTENT_EMPTY": ErrorDefinition(400, False, 400, "http"),
    "KB_IMAGE_DATA_URI_INVALID": ErrorDefinition(400, False, 400, "http"),
    "KB_IMAGE_DIMENSIONS_INVALID": ErrorDefinition(400, False, 400, "http"),
    "KB_IMAGE_DIMENSIONS_LIMIT": ErrorDefinition(400, False, 400, "http"),
    "KB_IMAGE_INVALID": ErrorDefinition(400, False, 400, "http"),
    "KB_IMAGE_MEDIA_TYPE_MISMATCH": ErrorDefinition(400, False, 400, "http"),
    "KB_IMAGE_MEDIA_TYPE_UNSUPPORTED": ErrorDefinition(400, False, 400, "http"),
    "KB_IMAGE_SIZE_LIMIT": ErrorDefinition(400, False, 400, "http"),
    "KB_JSON_DOCUMENT_INVALID": ErrorDefinition(400, False, 400, "http"),
    "KB_KNOWLEDGE_COPY_BUILTIN_METADATA_INVALID": ErrorDefinition(400, False, 400, "http"),
    "KB_KNOWLEDGE_COPY_METADATA_BUILTIN_CONFLICT": ErrorDefinition(
        400, False, 400, "http", {"field_name": str}
    ),
    "KB_KNOWLEDGE_COPY_METADATA_FIELD_INVALID": ErrorDefinition(
        400, False, 400, "http", {"field_name": str}
    ),
    "KB_KNOWLEDGE_COPY_METADATA_TENANT_INVALID": ErrorDefinition(400, False, 400, "http"),
    "KB_KNOWLEDGE_COPY_MODEL_UNAVAILABLE": ErrorDefinition(
        400, False, 400, "http", {"model_field": str}
    ),
    "KB_KNOWLEDGE_COPY_PARENT_INVALID": ErrorDefinition(400, False, 400, "http"),
    "KB_KNOWLEDGE_COPY_PARSER_CONFIG_INVALID": ErrorDefinition(400, False, 400, "http"),
    "KB_KNOWLEDGE_COPY_PERMISSION_UNSUPPORTED": ErrorDefinition(400, False, 400, "http"),
    "KB_KNOWLEDGE_COPY_TYPE_UNSUPPORTED": ErrorDefinition(400, False, 400, "http"),
    "KB_KNOWLEDGE_CREATOR_NOT_FOUND": ErrorDefinition(400, False, 10001, "business"),
    "KB_KNOWLEDGE_DOWNLOAD_EMPTY": ErrorDefinition(400, False, 404, "business"),
    "KB_KNOWLEDGE_NAME_EXISTS": ErrorDefinition(400, False, 400, "http", {"knowledge_name": str}),
    "KB_KNOWLEDGE_NOT_FOUND": ErrorDefinition(400, False, 404, "business"),
    "KB_KNOWLEDGE_PARSER_CONFIG_INVALID": ErrorDefinition(400, False, 400, "http"),
    "KB_KNOWLEDGE_SHARE_NOT_FOUND": ErrorDefinition(400, False, 404, "business"),
    "KB_KNOWLEDGE_SHARE_REFERENCE_INCOMPLETE": ErrorDefinition(400, False, 10001, "business"),
    "KB_METADATA_BATCH_KNOWLEDGE_MISMATCH": ErrorDefinition(400, False, 9104, "business"),
    "KB_METADATA_BUILTIN_NAME_CONFLICT": ErrorDefinition(
        400, False, 1001, "business", {"field_name": str}
    ),
    "KB_METADATA_DEFINITION_ID_MISSING": ErrorDefinition(400, False, 400, "http"),
    "KB_METADATA_FIELD_EXISTS": ErrorDefinition(409, False, 5001, "business", {"field_name": str}),
    "KB_METADATA_FIELD_UNDEFINED": ErrorDefinition(
        400, False, 1001, "business", {"field_name": str}
    ),
    "KB_METADATA_FIELD_UNKNOWN": ErrorDefinition(400, False, 400, "http", {"field_name": str}),
    "KB_METADATA_OPERATOR_UNSUPPORTED": ErrorDefinition(400, False, 400, "http"),
    "KB_METADATA_RESOURCE_NOT_FOUND": ErrorDefinition(400, False, 4006, "business"),
    "KB_METADATA_TIME_INVALID": ErrorDefinition(400, False, 400, "http"),
    "KB_METADATA_VALIDATION_ERROR": ErrorDefinition(400, False, 1001, "business"),
    "KB_METADATA_VALUE_TYPE_MISMATCH": ErrorDefinition(
        400, False, 1001, "business", {"field_name": str, "expected_type": str}
    ),
    "KB_MODEL_CONFIG_NOT_FOUND": ErrorDefinition(400, False, 404, "business"),
    "KB_MODEL_CREDENTIAL_UNAVAILABLE": ErrorDefinition(400, False, 400, "http"),
    "KB_PARENT_CHILD_CHUNK_TYPE_REQUIRED": ErrorDefinition(400, False, 400, "http"),
    "KB_PARENT_CHILD_MODE_DISABLED": ErrorDefinition(400, False, 400, "http"),
    "KB_PARENT_FOLDER_NOT_FOUND": ErrorDefinition(400, False, 404, "business"),
    "KB_PARENT_KNOWLEDGE_NOT_FOUND": ErrorDefinition(400, False, 404, "business"),
    "KB_PARSER_CONFIG_OBJECT_REQUIRED": ErrorDefinition(400, False, 400, "http"),
    "KB_PREVIEW_FILE_TYPE_UNSUPPORTED": ErrorDefinition(
        400, False, 400, "http", {"file_type": str}
    ),
    "KB_QA_EXPORT_EMPTY": ErrorDefinition(400, False, 404, "business"),
    "KB_QA_IMPORT_FILE_EMPTY": ErrorDefinition(400, False, 400, "http"),
    "KB_QA_IMPORT_FILE_TYPE_UNSUPPORTED": ErrorDefinition(400, False, 400, "http"),
    "KB_SELECTED_FILES_STORAGE_KEY_MISSING": ErrorDefinition(400, False, 404, "business"),
    "KB_SOURCE_KNOWLEDGE_NOT_FOUND": ErrorDefinition(400, False, 404, "business"),
    "KB_STORAGE_FILE_NOT_FOUND": ErrorDefinition(400, False, 404, "business"),
    "KB_TARGET_WORKSPACE_NOT_FOUND": ErrorDefinition(400, False, 404, "business"),
    "KB_TENANT_VISION_MODEL_NOT_AVAILABLE": ErrorDefinition(400, False, 10001, "business"),
    "KB_UPLOAD_CONTENT_EMPTY": ErrorDefinition(400, False, 400, "http"),
    "KB_UPLOAD_CONTENT_SIZE_LIMIT": ErrorDefinition(400, False, 400, "http"),
    "KB_UPLOAD_FILE_EMPTY": ErrorDefinition(400, False, 400, "http"),
    "KB_UPLOAD_FILE_SIZE_LIMIT": ErrorDefinition(400, False, 400, "http"),
    "KB_VISION_MODEL_OUTPUT_EMPTY": ErrorDefinition(400, False, 400, "http"),
    "KB_VISION_MODEL_UNAVAILABLE": ErrorDefinition(400, False, 400, "http"),
    "KB_WORKSPACE_EMBEDDING_MODEL_NOT_CONFIGURED": ErrorDefinition(400, False, 10001, "business"),
    "KB_WORKSPACE_LLM_MODEL_NOT_CONFIGURED": ErrorDefinition(400, False, 10001, "business"),
    "KB_WORKSPACE_NOT_FOUND": ErrorDefinition(400, False, 10001, "business"),
    "KB_WORKSPACE_RERANK_MODEL_NOT_CONFIGURED": ErrorDefinition(400, False, 10001, "business"),
    "KB_YUQUE_AUTH_INVALID": ErrorDefinition(200, False, 2001, "business"),
    "KB_GRAPH_DISABLED": ErrorDefinition(400, False, 400, "http"),
    "KB_GRAPH_PIPELINE_MISMATCH": ErrorDefinition(400, False, 400, "http"),
    "KB_IMAGE_AUTO_METADATA_UNSUPPORTED": ErrorDefinition(400, False, 400, "http"),
    "KB_IMAGE_EMBEDDING_MODEL_UNSUPPORTED": ErrorDefinition(400, False, 400, "http"),
    "KB_IMAGE_GLOBAL_RERANK_MODEL_UNSUPPORTED": ErrorDefinition(400, False, 400, "http"),
    "KB_IMAGE_HYBRID_RERANK_REQUIRED": ErrorDefinition(400, False, 400, "http"),
    "KB_IMAGE_PARTICIPLE_UNSUPPORTED": ErrorDefinition(400, False, 400, "http"),
    "KB_IMAGE_RERANK_MODEL_UNSUPPORTED": ErrorDefinition(400, False, 400, "http"),
    "KB_IMAGE_TARGET_CONFIG_UNSUPPORTED": ErrorDefinition(400, False, 400, "http"),
    "KB_IMAGE_WEIGHTED_GLOBAL_RERANK_UNSUPPORTED": ErrorDefinition(400, False, 400, "http"),
    "KB_MODEL_PROVIDER_RESPONSE_INVALID": ErrorDefinition(400, False, 10001, "business"),
    "KB_RETRIEVAL_EMBEDDING_MODEL_NOT_CONFIGURED": ErrorDefinition(400, False, 400, "http"),
    "KB_RETRIEVAL_GRAPH_LLM_NOT_CONFIGURED": ErrorDefinition(400, False, 400, "http"),
    "KB_RETRIEVAL_MODEL_CHANNEL_EXHAUSTED": ErrorDefinition(400, True, 400, "http"),
    "KB_RETRIEVAL_MODEL_CHANNEL_UNAVAILABLE": ErrorDefinition(400, True, 400, "http"),
    "KB_RETRIEVAL_MODEL_CREDENTIAL_INVALID": ErrorDefinition(400, False, 400, "http"),
    "KB_RETRIEVAL_MODEL_CREDENTIAL_UNAVAILABLE": ErrorDefinition(400, False, 400, "http"),
    "KB_RETRIEVAL_MODEL_INACTIVE": ErrorDefinition(400, False, 400, "http"),
    "KB_RETRIEVAL_MODEL_NOT_FOUND": ErrorDefinition(400, False, 400, "http"),
    "KB_RETRIEVAL_RERANK_MODEL_NOT_CONFIGURED": ErrorDefinition(400, False, 400, "http"),
    "KB_RETRIEVAL_TEXT_QUERY_REQUIRED": ErrorDefinition(400, False, 400, "http"),
    "KB_STORAGE_CONFIG_INVALID": ErrorDefinition(400, False, 10001, "business"),
    "KB_STORAGE_DELETE_FAILED": ErrorDefinition(400, False, 10001, "business"),
    "KB_STORAGE_DELETE_TIMEOUT": ErrorDefinition(400, True, 10001, "business"),
    "KB_STORAGE_DOWNLOAD_FAILED": ErrorDefinition(400, False, 10001, "business"),
    "KB_STORAGE_DOWNLOAD_TIMEOUT": ErrorDefinition(400, True, 10001, "business"),
    "KB_STORAGE_UPLOAD_FAILED": ErrorDefinition(400, False, 10001, "business"),
    "KB_STORAGE_UPLOAD_TIMEOUT": ErrorDefinition(400, True, 10001, "business"),
    "KB_WEIGHTED_RERANK_EMBEDDING_MISMATCH": ErrorDefinition(400, False, 400, "http"),
    "KB_WEIGHTED_RERANK_GRAPH_UNSUPPORTED": ErrorDefinition(400, False, 400, "http"),
    "KB_WEIGHTED_RERANK_REQUIRES_HYBRID": ErrorDefinition(400, False, 400, "http"),
    "KB_STORAGE_OPERATION_FAILED": ErrorDefinition(400, False, 10001, "business"),
    "KB_MULTIMODAL_EMBEDDING_CONNECTION_FAILED": ErrorDefinition(400, True, 10001, "business"),
    "KB_MULTIMODAL_EMBEDDING_RATE_LIMITED": ErrorDefinition(400, True, 10001, "business"),
    "KB_MULTIMODAL_EMBEDDING_RESPONSE_INVALID": ErrorDefinition(400, False, 10001, "business"),
    "KB_MULTIMODAL_EMBEDDING_TIMEOUT": ErrorDefinition(400, True, 10001, "business"),
    "KB_MULTIMODAL_RERANK_CONNECTION_FAILED": ErrorDefinition(400, True, 10001, "business"),
    "KB_MULTIMODAL_RERANK_RATE_LIMITED": ErrorDefinition(400, True, 10001, "business"),
    "KB_MULTIMODAL_RERANK_RESPONSE_INVALID": ErrorDefinition(400, False, 10001, "business"),
    "KB_MULTIMODAL_RERANK_TIMEOUT": ErrorDefinition(400, True, 10001, "business"),
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
            len(value) > MAX_PUBLIC_PARAM_LENGTH
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
        ):
            return False
        if isinstance(value, float) and not math.isfinite(value):
            return False
    return True


class KnowledgeError(Exception):
    """Untranslated cause; the catalog alone defines its wire contract."""

    def __init__(
        self,
        *,
        code: str,
        message: str | None = None,
        params: Mapping[str, PublicScalar] | None = None,
    ) -> None:
        # Excluded task callers still use exception text in progress messages.
        # Preserve that legacy argument; HTTP rendering only uses code and params.
        supplied = dict(params or {})
        if not valid_params(code, supplied):
            logger.error("Knowledge error definition invalid")
            code, supplied, message = "KB_INTERNAL_ERROR", {}, None
        self._legacy_message = message
        super().__init__(message if message is not None else str(code))
        self._code = str(code)
        self._params = MappingProxyType(supplied)

    @property
    def code(self) -> str:
        return self._code

    @property
    def params(self) -> Mapping[str, PublicScalar]:
        return self._params

    @property
    def message(self) -> str:
        return self._legacy_message if self._legacy_message is not None else self.code

    @property
    def status_code(self) -> int:
        return ERROR_DEFINITIONS[self.code].status_code

    @property
    def response_code(self) -> int:
        return ERROR_DEFINITIONS[self.code].response_code

    @property
    def retryable(self) -> bool:
        return ERROR_DEFINITIONS[self.code].retryable

    @property
    def response_style(self) -> ErrorResponseStyle:
        return ERROR_DEFINITIONS[self.code].response_style

    @classmethod
    def from_code(
        cls,
        code: str,
        message: str | None = None,
        *,
        params: Mapping[str, PublicScalar] | None = None,
    ) -> KnowledgeError:
        return cls(code=code, message=message, params=params)
