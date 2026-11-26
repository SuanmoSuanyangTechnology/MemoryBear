"""知识库公共错误契约。"""

from .definitions import (
    ERROR_DEFINITIONS,
    MAX_PUBLIC_PARAM_LENGTH,
    TRANSIENT_DEPENDENCY_HTTP_STATUSES,
    ErrorCode,
    ErrorDefinition,
    ErrorResponseStyle,
    KnowledgeError,
    PublicScalar,
    public_text,
    valid_params,
)

__all__ = [
    "ERROR_DEFINITIONS",
    "MAX_PUBLIC_PARAM_LENGTH",
    "TRANSIENT_DEPENDENCY_HTTP_STATUSES",
    "ErrorCode",
    "ErrorDefinition",
    "ErrorResponseStyle",
    "KnowledgeError",
    "PublicScalar",
    "public_text",
    "valid_params",
]
