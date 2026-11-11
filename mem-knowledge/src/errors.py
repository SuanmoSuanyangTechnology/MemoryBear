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

    grpc_status: str = "INTERNAL"

    def __post_init__(self) -> None:
        object.__setattr__(self, "params", MappingProxyType(dict(self.params)))


# 待评审的 4xx→5xx 保留旧 HTTP；业务码和 gRPC 原因独立表达。
_DEFINITIONS = {
    "KB_CONNECTOR_SOURCE_INVALID": ErrorDefinition(
        422, False, 9381, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_AUTH_DEPENDENCY_UNAVAILABLE": ErrorDefinition(
        401, False, 9343, "business", {}, "UNAVAILABLE"
    ),
    "KB_FAQ_INDEX_FAILED": ErrorDefinition(503, True, 9428, "business", {}, "UNAVAILABLE"),
    "KB_FAQ_IMAGE_QUERY_UNSUPPORTED": ErrorDefinition(
        422, False, 9315, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_FAQ_FILE_FILTER_UNSUPPORTED": ErrorDefinition(
        422, False, 9316, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_FAQ_AUTO_METADATA_UNSUPPORTED": ErrorDefinition(
        422, False, 9317, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_FAQ_METADATA_FILTER_UNSUPPORTED": ErrorDefinition(
        422, False, 9318, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_FAQ_MIXED_RETRIEVAL_UNSUPPORTED": ErrorDefinition(
        422, False, 9319, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_FAQ_DISPATCH_FAILED": ErrorDefinition(503, True, 9460, "business", {}, "UNAVAILABLE"),
    "KB_FAQ_WORKER_LOST": ErrorDefinition(503, True, 9460, "business", {}, "UNAVAILABLE"),
    "KB_FAQ_CONFLICT": ErrorDefinition(409, False, 9425, "business", {}, "ABORTED"),
    "KB_FAQ_CONTENT_INVALID": ErrorDefinition(
        409, False, 9429, "business", {}, "FAILED_PRECONDITION"
    ),
    "KB_FAQ_REVIEW_REQUIRED": ErrorDefinition(
        409, False, 9422, "business", {}, "FAILED_PRECONDITION"
    ),
    "KB_FAQ_SOURCE_UNAVAILABLE": ErrorDefinition(
        409, False, 9423, "business", {}, "FAILED_PRECONDITION"
    ),
    "KB_FAQ_SOURCE_CHANGED": ErrorDefinition(
        409, False, 9424, "business", {}, "FAILED_PRECONDITION"
    ),
    "KB_FAQ_JOB_BUSY": ErrorDefinition(409, False, 9426, "business", {}, "FAILED_PRECONDITION"),
    "KB_FAQ_JOB_TERMINAL": ErrorDefinition(409, False, 9427, "business", {}, "FAILED_PRECONDITION"),
    "KB_PRINCIPAL_INVALID": ErrorDefinition(401, False, 2001, "business", {}, "UNAUTHENTICATED"),
    "KB_VALIDATION_ERROR": ErrorDefinition(422, False, 1001, "business", {}, "INVALID_ARGUMENT"),
    "KB_RESOURCE_NOT_FOUND": ErrorDefinition(404, False, 4000, "business", {}, "NOT_FOUND"),
    "KB_CONFLICT": ErrorDefinition(409, False, 5004, "business", {}, "FAILED_PRECONDITION"),
    "KB_REFERENCE_NOT_FOUND": ErrorDefinition(400, False, 9455, "business", {}, "INTERNAL"),
    "KB_METADATA_TYPE_MISMATCH": ErrorDefinition(
        422, False, 9100, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_TASK_DISPATCH_FAILED": ErrorDefinition(400, True, 9460, "business", {}, "UNAVAILABLE"),
    "KB_WIKI_BACKFILL_DISPATCH_FAILED": ErrorDefinition(
        400, True, 9460, "business", {}, "UNAVAILABLE"
    ),
    "KB_WIKI_DISABLED": ErrorDefinition(409, False, 9401, "business", {}, "FAILED_PRECONDITION"),
    "KB_WIKI_SOURCE_NOT_READY": ErrorDefinition(
        409, True, 9402, "business", {}, "FAILED_PRECONDITION"
    ),
    "KB_WIKI_NOT_FOUND": ErrorDefinition(404, False, 4000, "business", {}, "NOT_FOUND"),
    "KB_WIKI_MODEL_REQUIRED": ErrorDefinition(
        409, False, 9330, "business", {}, "FAILED_PRECONDITION"
    ),
    "KB_STORAGE_UNAVAILABLE": ErrorDefinition(400, True, 9345, "business", {}, "UNAVAILABLE"),
    "KB_SEARCH_UNAVAILABLE": ErrorDefinition(400, True, 9348, "business", {}, "UNAVAILABLE"),
    "KB_EMBEDDING_TIMEOUT": ErrorDefinition(400, True, 9312, "business", {}, "UNAVAILABLE"),
    "KB_EMBEDDING_CONNECTION_FAILED": ErrorDefinition(
        400, True, 9312, "business", {}, "UNAVAILABLE"
    ),
    "KB_EMBEDDING_RATE_LIMITED": ErrorDefinition(400, True, 9312, "business", {}, "UNAVAILABLE"),
    "KB_EMBEDDING_REQUEST_FAILED": ErrorDefinition(400, False, 9313, "business", {}, "INTERNAL"),
    "KB_EMBEDDING_SERVICE_UNAVAILABLE": ErrorDefinition(
        400, True, 9312, "business", {}, "UNAVAILABLE"
    ),
    "KB_MODEL_UNAVAILABLE": ErrorDefinition(400, False, 10001, "business", {}, "INTERNAL"),
    "KB_MULTIMODAL_INPUT_LIMIT": ErrorDefinition(
        413, False, 8004, "business", {}, "RESOURCE_EXHAUSTED"
    ),
    "KB_MULTIMODAL_EMBEDDING_FAILED": ErrorDefinition(400, True, 9373, "business", {}, "INTERNAL"),
    "KB_MULTIMODAL_RERANK_FAILED": ErrorDefinition(400, True, 9393, "business", {}, "INTERNAL"),
    "KB_DATABASE_UNAVAILABLE": ErrorDefinition(400, True, 9443, "business", {}, "INTERNAL"),
    "KB_INTERNAL_ERROR": ErrorDefinition(500, False, 10001, "business", {}, "INTERNAL"),
    "KB_HTTP_UNAUTHORIZED": ErrorDefinition(401, False, 2001, "business", {}, "UNAUTHENTICATED"),
    "KB_HTTP_FORBIDDEN": ErrorDefinition(403, False, 3001, "business", {}, "PERMISSION_DENIED"),
    "KB_HTTP_METHOD_NOT_ALLOWED": ErrorDefinition(
        405, False, 9205, "business", {}, "UNIMPLEMENTED"
    ),
    "KB_HTTP_VALIDATION_ERROR": ErrorDefinition(
        422, False, 1001, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_HTTP_TOO_LARGE": ErrorDefinition(413, False, 8004, "business", {}, "RESOURCE_EXHAUSTED"),
    "KB_HTTP_RATE_LIMITED": ErrorDefinition(
        429, False, 10004, "business", {}, "RESOURCE_EXHAUSTED"
    ),
    "KB_HTTP_BAD_GATEWAY": ErrorDefinition(500, False, 9342, "business", {}, "INTERNAL"),
    "KB_HTTP_UNAVAILABLE": ErrorDefinition(503, False, 9341, "business", {}, "UNAVAILABLE"),
    "KB_HTTP_TIMEOUT": ErrorDefinition(503, False, 9340, "business", {}, "UNAVAILABLE"),
    "KB_RETRIEVAL_REQUEST_INVALID": ErrorDefinition(
        422, False, 1001, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_RERANK_CONFIG_INVALID": ErrorDefinition(
        422, False, 1001, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_BUILTIN_METADATA_FIELD_UNKNOWN": ErrorDefinition(
        422, False, 9100, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_BUILTIN_METADATA_OPERATOR_UNSUPPORTED": ErrorDefinition(
        422, False, 9100, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_CHILD_PARENT_ID_REQUIRED": ErrorDefinition(
        422, False, 1001, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_CHUNK_BATCH_LIMIT": ErrorDefinition(
        422, False, 9251, "business", {"max_count": int}, "INVALID_ARGUMENT"
    ),
    "KB_CHUNK_NOT_FOUND": ErrorDefinition(404, False, 4000, "business", {}, "NOT_FOUND"),
    "KB_DOCUMENT_CHUNK_MODE_CHANGE_FORBIDDEN": ErrorDefinition(
        409, False, 9404, "business", {}, "FAILED_PRECONDITION"
    ),
    "KB_DOCUMENT_NOT_FOUND": ErrorDefinition(404, False, 4005, "business", {}, "NOT_FOUND"),
    "KB_DOCUMENT_PARSER_CONFIG_INVALID": ErrorDefinition(
        422, False, 1001, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_EMBEDDING_MODEL_UNAVAILABLE": ErrorDefinition(
        409, False, 9310, "business", {}, "FAILED_PRECONDITION"
    ),
    "KB_EXTERNAL_ID_EXISTS": ErrorDefinition(409, False, 5002, "business", {}, "ALREADY_EXISTS"),
    "KB_FEISHU_AUTH_INVALID": ErrorDefinition(422, False, 9380, "business", {}, "INVALID_ARGUMENT"),
    "KB_FILE_NOT_FOUND": ErrorDefinition(404, False, 4006, "business", {}, "NOT_FOUND"),
    "KB_FILE_STORAGE_KEY_MISSING": ErrorDefinition(
        409, False, 9249, "business", {}, "FAILED_PRECONDITION"
    ),
    "KB_FILTER_LOGIC_INVALID": ErrorDefinition(
        422, False, 1001, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_GRAPH_CONFIG_INVALID": ErrorDefinition(
        422, False, 1001, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_GRAPH_ENTITY_TYPES_UNAVAILABLE": ErrorDefinition(
        400, False, 9485, "business", {}, "INTERNAL"
    ),
    "KB_GRAPH_NOT_ENABLED": ErrorDefinition(
        409, False, 9307, "business", {}, "FAILED_PRECONDITION"
    ),
    "KB_IMAGE_BASE64_INVALID": ErrorDefinition(
        422, False, 1001, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_IMAGE_CONTENT_EMPTY": ErrorDefinition(422, False, 1001, "business", {}, "INVALID_ARGUMENT"),
    "KB_IMAGE_DATA_URI_INVALID": ErrorDefinition(
        422, False, 1001, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_IMAGE_DIMENSIONS_INVALID": ErrorDefinition(
        422, False, 1001, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_IMAGE_DIMENSIONS_LIMIT": ErrorDefinition(
        413, False, 8004, "business", {}, "RESOURCE_EXHAUSTED"
    ),
    "KB_IMAGE_INVALID": ErrorDefinition(422, False, 1001, "business", {}, "INVALID_ARGUMENT"),
    "KB_IMAGE_MEDIA_TYPE_MISMATCH": ErrorDefinition(
        422, False, 1001, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_IMAGE_MEDIA_TYPE_UNSUPPORTED": ErrorDefinition(
        415, False, 9246, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_IMAGE_SIZE_LIMIT": ErrorDefinition(413, False, 8004, "business", {}, "RESOURCE_EXHAUSTED"),
    "KB_JSON_DOCUMENT_INVALID": ErrorDefinition(
        422, False, 1001, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_KNOWLEDGE_COPY_BUILTIN_METADATA_INVALID": ErrorDefinition(
        422, False, 1001, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_KNOWLEDGE_COPY_METADATA_BUILTIN_CONFLICT": ErrorDefinition(
        409, False, 5001, "business", {"field_name": str}, "ALREADY_EXISTS"
    ),
    "KB_KNOWLEDGE_COPY_METADATA_FIELD_INVALID": ErrorDefinition(
        422, False, 1001, "business", {"field_name": str}, "INVALID_ARGUMENT"
    ),
    "KB_KNOWLEDGE_COPY_METADATA_TENANT_INVALID": ErrorDefinition(
        422, False, 1001, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_KNOWLEDGE_COPY_MODEL_UNAVAILABLE": ErrorDefinition(
        400, False, 9305, "business", {"model_field": str}, "FAILED_PRECONDITION"
    ),
    "KB_KNOWLEDGE_COPY_PARENT_INVALID": ErrorDefinition(
        422, False, 1001, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_KNOWLEDGE_COPY_PARSER_CONFIG_INVALID": ErrorDefinition(
        422, False, 1001, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_KNOWLEDGE_COPY_PERMISSION_UNSUPPORTED": ErrorDefinition(
        422, False, 1001, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_KNOWLEDGE_COPY_TYPE_UNSUPPORTED": ErrorDefinition(
        422, False, 1001, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_KNOWLEDGE_CREATOR_NOT_FOUND": ErrorDefinition(400, False, 9450, "business", {}, "INTERNAL"),
    "KB_KNOWLEDGE_DOWNLOAD_EMPTY": ErrorDefinition(
        409, False, 9248, "business", {}, "FAILED_PRECONDITION"
    ),
    "KB_KNOWLEDGE_NAME_EXISTS": ErrorDefinition(
        409, False, 5001, "business", {"knowledge_name": str}, "ALREADY_EXISTS"
    ),
    "KB_KNOWLEDGE_NOT_FOUND": ErrorDefinition(404, False, 4004, "business", {}, "NOT_FOUND"),
    "KB_KNOWLEDGE_MODEL_UNAVAILABLE": ErrorDefinition(
        400, False, 9305, "business", {"model_field": str}, "FAILED_PRECONDITION"
    ),
    "KB_KNOWLEDGE_MODEL_CAPABILITY_MISMATCH": ErrorDefinition(
        400, False, 9305, "business", {"model_field": str}, "FAILED_PRECONDITION"
    ),
    "KB_KNOWLEDGE_PARSER_CONFIG_INVALID": ErrorDefinition(
        422, False, 1001, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_KNOWLEDGE_SHARE_NOT_FOUND": ErrorDefinition(404, False, 4000, "business", {}, "NOT_FOUND"),
    "KB_KNOWLEDGE_SHARE_REFERENCE_INCOMPLETE": ErrorDefinition(
        400, False, 9451, "business", {}, "INTERNAL"
    ),
    "KB_METADATA_BATCH_KNOWLEDGE_MISMATCH": ErrorDefinition(
        422, False, 9104, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_METADATA_BUILTIN_NAME_CONFLICT": ErrorDefinition(
        409, False, 5001, "business", {"field_name": str}, "ALREADY_EXISTS"
    ),
    "KB_METADATA_DEFINITION_ID_MISSING": ErrorDefinition(
        422, False, 1001, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_METADATA_FIELD_EXISTS": ErrorDefinition(
        409, False, 5001, "business", {"field_name": str}, "ALREADY_EXISTS"
    ),
    "KB_METADATA_FIELD_UNDEFINED": ErrorDefinition(
        422, False, 9100, "business", {"field_name": str}, "INVALID_ARGUMENT"
    ),
    "KB_METADATA_FIELD_UNKNOWN": ErrorDefinition(
        422, False, 9100, "business", {"field_name": str}, "INVALID_ARGUMENT"
    ),
    "KB_METADATA_OPERATOR_UNSUPPORTED": ErrorDefinition(
        422, False, 9100, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_METADATA_RESOURCE_NOT_FOUND": ErrorDefinition(
        404, False, 4000, "business", {}, "NOT_FOUND"
    ),
    "KB_METADATA_TIME_INVALID": ErrorDefinition(
        422, False, 9100, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_METADATA_VALIDATION_ERROR": ErrorDefinition(
        422, False, 9100, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_METADATA_VALUE_TYPE_MISMATCH": ErrorDefinition(
        422, False, 9100, "business", {"field_name": str, "expected_type": str}, "INVALID_ARGUMENT"
    ),
    "KB_MODEL_CONFIG_NOT_FOUND": ErrorDefinition(
        409, False, 9482, "business", {}, "FAILED_PRECONDITION"
    ),
    "KB_MODEL_CREDENTIAL_UNAVAILABLE": ErrorDefinition(
        409, False, 9305, "business", {}, "FAILED_PRECONDITION"
    ),
    "KB_PARENT_CHILD_CHUNK_TYPE_REQUIRED": ErrorDefinition(
        422, False, 1001, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_PARENT_CHILD_MODE_DISABLED": ErrorDefinition(
        409, False, 9405, "business", {}, "FAILED_PRECONDITION"
    ),
    "KB_PARENT_FOLDER_NOT_FOUND": ErrorDefinition(404, False, 4000, "business", {}, "NOT_FOUND"),
    "KB_PARENT_KNOWLEDGE_NOT_FOUND": ErrorDefinition(404, False, 4004, "business", {}, "NOT_FOUND"),
    "KB_PARSER_CONFIG_OBJECT_REQUIRED": ErrorDefinition(
        422, False, 1001, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_PREVIEW_FILE_TYPE_UNSUPPORTED": ErrorDefinition(
        422, False, 9250, "business", {"file_type": str}, "INVALID_ARGUMENT"
    ),
    "KB_QA_EXPORT_EMPTY": ErrorDefinition(409, False, 9248, "business", {}, "FAILED_PRECONDITION"),
    "KB_QA_IMPORT_FILE_EMPTY": ErrorDefinition(
        422, False, 9245, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_QA_IMPORT_FILE_TYPE_UNSUPPORTED": ErrorDefinition(
        415, False, 9246, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_SELECTED_FILES_STORAGE_KEY_MISSING": ErrorDefinition(
        409, False, 9249, "business", {}, "FAILED_PRECONDITION"
    ),
    "KB_SOURCE_KNOWLEDGE_NOT_FOUND": ErrorDefinition(404, False, 4004, "business", {}, "NOT_FOUND"),
    "KB_STORAGE_FILE_NOT_FOUND": ErrorDefinition(400, False, 9452, "business", {}, "INTERNAL"),
    "KB_TARGET_WORKSPACE_NOT_FOUND": ErrorDefinition(404, False, 4002, "business", {}, "NOT_FOUND"),
    "KB_TENANT_VISION_MODEL_NOT_AVAILABLE": ErrorDefinition(
        409, False, 9320, "business", {}, "FAILED_PRECONDITION"
    ),
    "KB_UPLOAD_CONTENT_EMPTY": ErrorDefinition(
        422, False, 1001, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_UPLOAD_CONTENT_SIZE_LIMIT": ErrorDefinition(
        413, False, 8004, "business", {}, "RESOURCE_EXHAUSTED"
    ),
    "KB_UPLOAD_FILE_EMPTY": ErrorDefinition(422, False, 9245, "business", {}, "INVALID_ARGUMENT"),
    "KB_UPLOAD_FILE_SIZE_LIMIT": ErrorDefinition(
        413, False, 8004, "business", {}, "RESOURCE_EXHAUSTED"
    ),
    "KB_VISION_MODEL_OUTPUT_EMPTY": ErrorDefinition(400, False, 9323, "business", {}, "INTERNAL"),
    "KB_VISION_MODEL_UNAVAILABLE": ErrorDefinition(
        409, False, 9320, "business", {}, "FAILED_PRECONDITION"
    ),
    "KB_WORKSPACE_EMBEDDING_MODEL_NOT_CONFIGURED": ErrorDefinition(
        409, False, 9310, "business", {}, "FAILED_PRECONDITION"
    ),
    "KB_WORKSPACE_LLM_MODEL_NOT_CONFIGURED": ErrorDefinition(
        409, False, 9330, "business", {}, "FAILED_PRECONDITION"
    ),
    "KB_WORKSPACE_NOT_FOUND": ErrorDefinition(404, False, 4002, "business", {}, "NOT_FOUND"),
    "KB_WORKSPACE_RERANK_MODEL_NOT_CONFIGURED": ErrorDefinition(
        409, False, 9360, "business", {}, "FAILED_PRECONDITION"
    ),
    "KB_YUQUE_AUTH_INVALID": ErrorDefinition(422, False, 9380, "business", {}, "INVALID_ARGUMENT"),
    "KB_GRAPH_DISABLED": ErrorDefinition(409, False, 9307, "business", {}, "FAILED_PRECONDITION"),
    "KB_GRAPH_PIPELINE_MISMATCH": ErrorDefinition(
        409, False, 9308, "business", {}, "FAILED_PRECONDITION"
    ),
    "KB_IMAGE_AUTO_METADATA_UNSUPPORTED": ErrorDefinition(
        422, False, 9383, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_IMAGE_EMBEDDING_MODEL_UNSUPPORTED": ErrorDefinition(
        422, False, 9371, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_IMAGE_GLOBAL_RERANK_MODEL_UNSUPPORTED": ErrorDefinition(
        422, False, 9391, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_IMAGE_HYBRID_RERANK_REQUIRED": ErrorDefinition(
        422, False, 9394, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_IMAGE_PARTICIPLE_UNSUPPORTED": ErrorDefinition(
        422, False, 9384, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_IMAGE_RERANK_MODEL_UNSUPPORTED": ErrorDefinition(
        422, False, 9391, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_IMAGE_TARGET_CONFIG_UNSUPPORTED": ErrorDefinition(
        422, False, 9387, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_IMAGE_WEIGHTED_GLOBAL_RERANK_UNSUPPORTED": ErrorDefinition(
        422, False, 9388, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_MODEL_PROVIDER_RESPONSE_INVALID": ErrorDefinition(
        400, False, 9342, "business", {}, "INTERNAL"
    ),
    "KB_RETRIEVAL_EMBEDDING_MODEL_NOT_CONFIGURED": ErrorDefinition(
        409, False, 9310, "business", {}, "FAILED_PRECONDITION"
    ),
    "KB_RETRIEVAL_GRAPH_LLM_NOT_CONFIGURED": ErrorDefinition(
        409, False, 9482, "business", {}, "FAILED_PRECONDITION"
    ),
    "KB_RETRIEVAL_MODEL_CHANNEL_EXHAUSTED": ErrorDefinition(
        400, False, 9305, "business", {}, "FAILED_PRECONDITION"
    ),
    "KB_RETRIEVAL_MODEL_CHANNEL_UNAVAILABLE": ErrorDefinition(
        400, False, 9305, "business", {}, "FAILED_PRECONDITION"
    ),
    "KB_RETRIEVAL_MODEL_CREDENTIAL_INVALID": ErrorDefinition(
        400, False, 9305, "business", {}, "FAILED_PRECONDITION"
    ),
    "KB_RETRIEVAL_MODEL_CREDENTIAL_UNAVAILABLE": ErrorDefinition(
        400, False, 9305, "business", {}, "FAILED_PRECONDITION"
    ),
    "KB_RETRIEVAL_MODEL_INACTIVE": ErrorDefinition(
        400, False, 9305, "business", {}, "FAILED_PRECONDITION"
    ),
    "KB_RETRIEVAL_MODEL_NOT_FOUND": ErrorDefinition(
        400, False, 9305, "business", {}, "FAILED_PRECONDITION"
    ),
    "KB_RETRIEVAL_RERANK_MODEL_NOT_CONFIGURED": ErrorDefinition(
        409, False, 9360, "business", {}, "FAILED_PRECONDITION"
    ),
    "KB_RETRIEVAL_TEXT_QUERY_REQUIRED": ErrorDefinition(
        422, False, 1001, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_STORAGE_CONFIG_INVALID": ErrorDefinition(400, False, 9444, "business", {}, "INTERNAL"),
    "KB_STORAGE_DELETE_FAILED": ErrorDefinition(400, False, 9445, "business", {}, "INTERNAL"),
    "KB_STORAGE_DELETE_TIMEOUT": ErrorDefinition(400, True, 9346, "business", {}, "UNAVAILABLE"),
    "KB_STORAGE_DOWNLOAD_FAILED": ErrorDefinition(400, False, 9445, "business", {}, "INTERNAL"),
    "KB_STORAGE_DOWNLOAD_TIMEOUT": ErrorDefinition(400, True, 9346, "business", {}, "UNAVAILABLE"),
    "KB_STORAGE_UPLOAD_FAILED": ErrorDefinition(400, False, 9445, "business", {}, "INTERNAL"),
    "KB_STORAGE_UPLOAD_TIMEOUT": ErrorDefinition(400, True, 9346, "business", {}, "UNAVAILABLE"),
    "KB_WEIGHTED_RERANK_EMBEDDING_MISMATCH": ErrorDefinition(
        422, False, 9314, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_WEIGHTED_RERANK_GRAPH_UNSUPPORTED": ErrorDefinition(
        422, False, 9309, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_WEIGHTED_RERANK_REQUIRES_HYBRID": ErrorDefinition(
        422, False, 9306, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_STORAGE_OPERATION_FAILED": ErrorDefinition(400, False, 9445, "business", {}, "INTERNAL"),
    "KB_MULTIMODAL_EMBEDDING_CONNECTION_FAILED": ErrorDefinition(
        400, True, 9372, "business", {}, "UNAVAILABLE"
    ),
    "KB_MULTIMODAL_EMBEDDING_RATE_LIMITED": ErrorDefinition(
        400, True, 9372, "business", {}, "UNAVAILABLE"
    ),
    "KB_MULTIMODAL_EMBEDDING_RESPONSE_INVALID": ErrorDefinition(
        400, False, 9373, "business", {}, "INTERNAL"
    ),
    "KB_MULTIMODAL_EMBEDDING_TIMEOUT": ErrorDefinition(
        400, True, 9372, "business", {}, "UNAVAILABLE"
    ),
    "KB_MULTIMODAL_RERANK_CONNECTION_FAILED": ErrorDefinition(
        400, True, 9392, "business", {}, "UNAVAILABLE"
    ),
    "KB_MULTIMODAL_RERANK_RATE_LIMITED": ErrorDefinition(
        400, True, 9392, "business", {}, "UNAVAILABLE"
    ),
    "KB_MULTIMODAL_RERANK_RESPONSE_INVALID": ErrorDefinition(
        400, False, 9393, "business", {}, "INTERNAL"
    ),
    "KB_MULTIMODAL_RERANK_TIMEOUT": ErrorDefinition(400, True, 9392, "business", {}, "UNAVAILABLE"),
    "KB_IMAGE_EMBEDDING_MODEL_CONFIG_INVALID": ErrorDefinition(
        409, False, 9370, "business", {}, "FAILED_PRECONDITION"
    ),
    "KB_IMAGE_EMBEDDING_MODEL_CAPABILITY_MISMATCH": ErrorDefinition(
        422, False, 9371, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_IMAGE_EMBEDDING_MODEL_PROCESSING_FAILED": ErrorDefinition(
        400, False, 9373, "business", {}, "INTERNAL"
    ),
    "KB_EMBEDDING_MODEL_CONFIG_INVALID": ErrorDefinition(
        409, False, 9310, "business", {}, "FAILED_PRECONDITION"
    ),
    "KB_EMBEDDING_MODEL_CAPABILITY_MISMATCH": ErrorDefinition(
        422, False, 9311, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_EMBEDDING_MODEL_PROCESSING_FAILED": ErrorDefinition(
        400, False, 9313, "business", {}, "INTERNAL"
    ),
    "KB_REQUEST_VALIDATION": ErrorDefinition(422, False, 1001, "business", {}, "INVALID_ARGUMENT"),
    "KB_TASK_NAME_CONFIG_INVALID": ErrorDefinition(400, False, 9440, "business", {}, "INTERNAL"),
    "KB_TASK_QUEUE_CONFIG_INVALID": ErrorDefinition(400, False, 9440, "business", {}, "INTERNAL"),
    "KB_WEIGHTED_SCORING_QUERY_REQUIRED": ErrorDefinition(
        422, False, 1001, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_AUTH_PRINCIPAL_INVALID": ErrorDefinition(
        401, False, 2001, "business", {}, "UNAUTHENTICATED"
    ),
    "KB_SOURCE_HEADER_INVALID": ErrorDefinition(
        422, False, 1001, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_PRINCIPAL_REFERENCE_INVALID": ErrorDefinition(400, False, 9454, "business", {}, "INTERNAL"),
    "KB_RETRIEVAL_KNOWLEDGE_NOT_FOUND": ErrorDefinition(
        404, False, 4004, "business", {}, "NOT_FOUND"
    ),
    "KB_FAQ_NOT_FOUND": ErrorDefinition(404, False, 4000, "business", {}, "NOT_FOUND"),
    "KB_FAQ_VERSION_CONFLICT": ErrorDefinition(409, False, 9425, "business", {}, "ABORTED"),
    "KB_FAQ_SOURCE_UNAVAILABLE_FROM_WRAPPER": ErrorDefinition(
        409, False, 9423, "business", {}, "FAILED_PRECONDITION"
    ),
    "KB_FAQ_LOAD_FAILED": ErrorDefinition(400, False, 9441, "business", {}, "INTERNAL"),
    "KB_STATE_CONFLICT": ErrorDefinition(409, False, 5004, "business", {}, "FAILED_PRECONDITION"),
    "KB_WIKI_SOURCE_CHANGED": ErrorDefinition(
        409, False, 9424, "business", {}, "FAILED_PRECONDITION"
    ),
    "KB_WIKI_CONFIG_CHANGED": ErrorDefinition(
        409, False, 9430, "business", {}, "FAILED_PRECONDITION"
    ),
    "KB_WIKI_MATERIAL_PLAN_MISMATCH": ErrorDefinition(
        409, False, 9430, "business", {}, "FAILED_PRECONDITION"
    ),
    "KB_WIKI_TAXONOMY_CONFLICT": ErrorDefinition(
        409, False, 9431, "business", {}, "FAILED_PRECONDITION"
    ),
    "KB_WIKI_IDENTITY_AMBIGUOUS": ErrorDefinition(
        409, False, 9432, "business", {}, "FAILED_PRECONDITION"
    ),
    "KB_WIKI_BUDGET_EXCEEDED": ErrorDefinition(
        422, False, 9433, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_LLM_MODEL_PROCESSING_FAILED": ErrorDefinition(400, False, 9333, "business", {}, "INTERNAL"),
    "KB_WIKI_EXECUTION_FAILED": ErrorDefinition(409, False, 9442, "business", {}, "INTERNAL"),
    "KB_WIKI_UNMAPPED_ERROR": ErrorDefinition(409, False, 9442, "business", {}, "INTERNAL"),
    "KB_RERANK_MODEL_CONFIG_INVALID": ErrorDefinition(
        409, False, 9360, "business", {}, "FAILED_PRECONDITION"
    ),
    "KB_LLM_MODEL_CONFIG_INVALID": ErrorDefinition(
        409, False, 9330, "business", {}, "FAILED_PRECONDITION"
    ),
    "KB_VISION_MODEL_CONFIG_INVALID": ErrorDefinition(
        409, False, 9320, "business", {}, "FAILED_PRECONDITION"
    ),
    "KB_AUDIO_TRANSCRIPTION_MODEL_CONFIG_INVALID": ErrorDefinition(
        409, False, 9486, "business", {}, "FAILED_PRECONDITION"
    ),
    "KB_VIDEO_UNDERSTANDING_MODEL_CONFIG_INVALID": ErrorDefinition(
        409, False, 9490, "business", {}, "FAILED_PRECONDITION"
    ),
    "KB_RERANK_MODEL_CAPABILITY_MISMATCH": ErrorDefinition(
        422, False, 9361, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_LLM_MODEL_CAPABILITY_MISMATCH": ErrorDefinition(
        422, False, 9331, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_VISION_MODEL_CAPABILITY_MISMATCH": ErrorDefinition(
        422, False, 9321, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_AUDIO_TRANSCRIPTION_MODEL_CAPABILITY_MISMATCH": ErrorDefinition(
        422, False, 9487, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_VIDEO_UNDERSTANDING_MODEL_CAPABILITY_MISMATCH": ErrorDefinition(
        422, False, 9491, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_MODEL_CREDENTIAL_INVALID": ErrorDefinition(
        409, False, 9305, "business", {}, "FAILED_PRECONDITION"
    ),
    "KB_EMBEDDING_MODEL_TEMPORARILY_UNAVAILABLE": ErrorDefinition(
        400, True, 9312, "business", {}, "UNAVAILABLE"
    ),
    "KB_RERANK_MODEL_TEMPORARILY_UNAVAILABLE": ErrorDefinition(
        400, True, 9362, "business", {}, "UNAVAILABLE"
    ),
    "KB_LLM_MODEL_TEMPORARILY_UNAVAILABLE": ErrorDefinition(
        400, True, 9332, "business", {}, "UNAVAILABLE"
    ),
    "KB_DATABASE_CONNECTION_UNAVAILABLE": ErrorDefinition(
        400, True, 10002, "business", {}, "UNAVAILABLE"
    ),
    "KB_DATABASE_OPERATION_FAILED": ErrorDefinition(400, False, 9443, "business", {}, "INTERNAL"),
    "KB_FAQ_SOURCE_SNAPSHOT_UNAVAILABLE": ErrorDefinition(
        409, False, 9403, "business", {}, "FAILED_PRECONDITION"
    ),
    "KB_FAQ_INPUT_INVALID": ErrorDefinition(422, False, 9421, "business", {}, "INVALID_ARGUMENT"),
    "KB_FAQ_CONTENT_CONFLICT": ErrorDefinition(
        409, False, 9429, "business", {}, "FAILED_PRECONDITION"
    ),
    "KB_FAQ_STORED_CONTENT_INVALID": ErrorDefinition(409, False, 9453, "business", {}, "INTERNAL"),
    "KB_TASK_BROKER_UNAVAILABLE": ErrorDefinition(400, True, 9460, "business", {}, "UNAVAILABLE"),
    "KB_TASK_DISPATCH_CONFIG_INVALID": ErrorDefinition(
        400, False, 9440, "business", {}, "INTERNAL"
    ),
    "KB_BACKFILL_TASK_NOT_ACCEPTED": ErrorDefinition(
        400, True, 9460, "business", {}, "UNAVAILABLE"
    ),
    "KB_RERANK_MODEL_PROCESSING_FAILED": ErrorDefinition(
        400, False, 9363, "business", {}, "INTERNAL"
    ),
    "KB_IMAGE_RERANK_MODEL_CONFIG_INVALID": ErrorDefinition(
        409, False, 9390, "business", {}, "FAILED_PRECONDITION"
    ),
    "KB_IMAGE_RERANK_MODEL_CAPABILITY_MISMATCH": ErrorDefinition(
        422, False, 9391, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_IMAGE_RERANK_MODEL_PROCESSING_FAILED": ErrorDefinition(
        400, False, 9393, "business", {}, "INTERNAL"
    ),
    "KB_GRAPH_ENTITY_MODEL_NOT_CONFIGURED": ErrorDefinition(
        409, False, 9482, "business", {}, "FAILED_PRECONDITION"
    ),
    "KB_GRAPH_ENTITY_MODEL_UNAVAILABLE": ErrorDefinition(
        409, False, 9482, "business", {}, "FAILED_PRECONDITION"
    ),
    "KB_GRAPH_ENTITY_MODEL_TEMPORARILY_UNAVAILABLE": ErrorDefinition(
        400, True, 9484, "business", {}, "UNAVAILABLE"
    ),
    "KB_GRAPH_ENTITY_MODEL_PROCESSING_FAILED": ErrorDefinition(
        400, False, 9485, "business", {}, "INTERNAL"
    ),
    "KB_STORAGE_DELETE_AUTH_OR_CONFIG_INVALID": ErrorDefinition(
        400, False, 9444, "business", {}, "INTERNAL"
    ),
    "KB_STORAGE_DELETE_TRANSIENT": ErrorDefinition(400, True, 9345, "business", {}, "UNAVAILABLE"),
    "KB_STORAGE_DELETE_UNEXPECTED": ErrorDefinition(400, False, 9445, "business", {}, "INTERNAL"),
    "KB_STORAGE_DOWNLOAD_AUTH_OR_CONFIG_INVALID": ErrorDefinition(
        400, False, 9444, "business", {}, "INTERNAL"
    ),
    "KB_STORAGE_DOWNLOAD_TRANSIENT": ErrorDefinition(
        400, True, 9345, "business", {}, "UNAVAILABLE"
    ),
    "KB_STORAGE_DOWNLOAD_UNEXPECTED": ErrorDefinition(400, False, 9445, "business", {}, "INTERNAL"),
    "KB_STORAGE_UPLOAD_AUTH_OR_CONFIG_INVALID": ErrorDefinition(
        400, False, 9444, "business", {}, "INTERNAL"
    ),
    "KB_STORAGE_UPLOAD_TRANSIENT": ErrorDefinition(400, True, 9345, "business", {}, "UNAVAILABLE"),
    "KB_STORAGE_UPLOAD_UNEXPECTED": ErrorDefinition(400, False, 9445, "business", {}, "INTERNAL"),
    "KB_STORAGE_OPERATION_AUTH_OR_CONFIG_INVALID": ErrorDefinition(
        400, False, 9444, "business", {}, "INTERNAL"
    ),
    "KB_STORAGE_OPERATION_TRANSIENT": ErrorDefinition(
        400, True, 9345, "business", {}, "UNAVAILABLE"
    ),
    "KB_STORAGE_OPERATION_UNEXPECTED": ErrorDefinition(
        400, False, 9445, "business", {}, "INTERNAL"
    ),
    "KB_FILE_RECORD_NOT_FOUND": ErrorDefinition(404, False, 4006, "business", {}, "NOT_FOUND"),
    "KB_STORAGE_OBJECT_MISSING": ErrorDefinition(400, False, 9452, "business", {}, "INTERNAL"),
    "KB_STORAGE_DEPENDENCY_UNAVAILABLE": ErrorDefinition(
        400, True, 9345, "business", {}, "UNAVAILABLE"
    ),
    "KB_STORAGE_OPERATION_TIMEOUT": ErrorDefinition(400, True, 9346, "business", {}, "UNAVAILABLE"),
    "KB_VISION_MODEL_TEMPORARILY_UNAVAILABLE": ErrorDefinition(
        400, True, 9322, "business", {}, "UNAVAILABLE"
    ),
    "KB_VISION_MODEL_PROCESSING_FAILED": ErrorDefinition(
        400, False, 9323, "business", {}, "INTERNAL"
    ),
    "KB_IMAGE_EMBEDDING_MODEL_TEMPORARILY_UNAVAILABLE": ErrorDefinition(
        400, True, 9372, "business", {}, "UNAVAILABLE"
    ),
    "KB_IMAGE_RERANK_MODEL_TEMPORARILY_UNAVAILABLE": ErrorDefinition(
        400, True, 9392, "business", {}, "UNAVAILABLE"
    ),
    "KB_GRAPH_ENTITY_MODEL_CONFIG_INVALID": ErrorDefinition(
        409, False, 9482, "business", {}, "FAILED_PRECONDITION"
    ),
    "KB_GRAPH_ENTITY_MODEL_CAPABILITY_MISMATCH": ErrorDefinition(
        422, False, 9483, "business", {}, "INVALID_ARGUMENT"
    ),
    "KB_AUDIO_TRANSCRIPTION_MODEL_TEMPORARILY_UNAVAILABLE": ErrorDefinition(
        400, True, 9488, "business", {}, "UNAVAILABLE"
    ),
    "KB_AUDIO_TRANSCRIPTION_MODEL_PROCESSING_FAILED": ErrorDefinition(
        400, False, 9489, "business", {}, "INTERNAL"
    ),
    "KB_VIDEO_UNDERSTANDING_MODEL_TEMPORARILY_UNAVAILABLE": ErrorDefinition(
        400, True, 9492, "business", {}, "UNAVAILABLE"
    ),
    "KB_VIDEO_UNDERSTANDING_MODEL_PROCESSING_FAILED": ErrorDefinition(
        400, False, 9493, "business", {}, "INTERNAL"
    ),
    "KB_REQUEST_MALFORMED": ErrorDefinition(400, False, 1000, "business", {}, "INVALID_ARGUMENT"),
    "KB_ROUTE_NOT_FOUND": ErrorDefinition(404, False, 4000, "business", {}, "NOT_FOUND"),
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
        status_code: int | None = None,
        diagnostic: str = "",
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
        self._status_code = status_code
        self.diagnostic = diagnostic

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
        return self._status_code or ERROR_DEFINITIONS[self.code].status_code

    @property
    def grpc_status(self) -> str:
        return ERROR_DEFINITIONS[self.code].grpc_status

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
        status_code: int | None = None,
        diagnostic: str = "",
    ) -> KnowledgeError:
        return cls(
            code=code,
            message=message,
            params=params,
            status_code=status_code,
            diagnostic=diagnostic,
        )
