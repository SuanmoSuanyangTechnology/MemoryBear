"""Frozen error-code catalog shared by every service."""

from __future__ import annotations

from enum import IntEnum


class BizCode(IntEnum):
    # shared: success / request / content safety (0, 1xxx)
    OK = 0
    BAD_REQUEST = 1000
    VALIDATION_FAILED = 1001
    MISSING_PARAMETER = 1002
    INVALID_PARAMETER = 1003
    CONTENT_BLOCKED = 1010

    # authentication (2xxx)
    UNAUTHORIZED = 2001
    TOKEN_INVALID = 2002
    TOKEN_EXPIRED = 2003
    TOKEN_BLACKLISTED = 2004
    PASSWORD_ERROR = 2005
    LOGIN_FAILED = 2006

    # authorization / api keys / quota (3xxx)
    FORBIDDEN = 3001
    TENANT_NOT_FOUND = 3002
    WORKSPACE_NO_ACCESS = 3003
    WORKSPACE_INVITE_NOT_FOUND = 3004
    WORKSPACE_ACCESS_DENIED = 3005
    API_KEY_NOT_FOUND = 3007
    API_KEY_DUPLICATE_NAME = 3008
    API_KEY_INVALID = 3009
    API_KEY_EXPIRED = 3010
    API_KEY_INACTIVE = 3011
    API_KEY_INVALID_SCOPE = 3012
    API_KEY_INVALID_RESOURCE = 3013
    API_KEY_QPS_LIMIT_EXCEEDED = 3014
    API_KEY_DAILY_LIMIT_EXCEEDED = 3015
    API_KEY_QUOTA_EXCEEDED = 3016
    API_KEY_RATE_LIMIT_EXCEEDED = 3017
    QUOTA_EXCEEDED = 3018
    RATE_LIMIT_EXCEEDED = 3019

    # shared resources + model channels (4xxx)
    NOT_FOUND = 4000
    USER_NOT_FOUND = 4001
    WORKSPACE_NOT_FOUND = 4002
    MODEL_NOT_FOUND = 4003
    KNOWLEDGE_NOT_FOUND = 4004
    DOCUMENT_NOT_FOUND = 4005
    FILE_NOT_FOUND = 4006
    APP_NOT_FOUND = 4007
    RELEASE_NOT_FOUND = 4008
    USER_NO_ACCESS = 4009
    MODEL_DEPRECATED = 4010
    CHANNEL_DISABLED = 4011
    NO_AVAILABLE_CHANNEL = 4012
    SPEEDBEAR_CHANNEL_MISSING = 4013
    CREDENTIAL_DECRYPT_ERROR = 4014
    MODEL_AVAILABLE_IN_PLAZA = 4015

    # conflicts / state (5xxx)
    DUPLICATE_NAME = 5001
    RESOURCE_ALREADY_EXISTS = 5002
    VERSION_ALREADY_EXISTS = 5003
    STATE_CONFLICT = 5004
    RESOURCE_IN_USE = 5005
    VERSION_LIMIT_EXCEEDED = 5006

    # application-service (6xxx)
    PUBLISH_FAILED = 6001
    NO_DRAFT_TO_PUBLISH = 6002
    ROLLBACK_TARGET_NOT_FOUND = 6003
    APP_TYPE_NOT_SUPPORTED = 6004
    AGENT_CONFIG_MISSING = 6005
    SHARE_DISABLED = 6006
    INVALID_PASSWORD = 6007
    PASSWORD_REQUIRED = 6008
    EMBED_NOT_ALLOWED = 6009
    PERMISSION_DENIED = 6010
    INVALID_CONVERSATION = 6011
    CONFIG_MISSING = 6012
    APP_NOT_PUBLISHED = 6013
    MODEL_NOT_CONFIGURED = 6014
    TOOL_NOT_FOUND = 6015

    # external catalog / community market (6020-6028)
    FEATURE_NOT_AVAILABLE = 6020
    EXTERNAL_CATALOG_UNAVAILABLE = 6021
    EXTERNAL_CATALOG_NOT_FOUND = 6022
    EXTERNAL_CATALOG_CONFLICT = 6023
    EXTERNAL_CATALOG_INVALID = 6024
    TOOL_OPERATION_INVALID = 6025
    TOOL_SELECTION_INACTIVE = 6026
    TOOL_METHODS_UNAVAILABLE = 6027
    REVIEW_STATE_CONFLICT = 6028

    # model-service (7xxx)
    MODEL_CONFIG_INVALID = 7001
    API_KEY_MISSING = 7002
    PROVIDER_NOT_SUPPORTED = 7003
    LLM_ERROR = 7004
    EMBEDDING_ERROR = 7005
    UPSTREAM_UNKNOWN = 7006

    # monolith / file parsing (8xxx)
    FILE_READ_ERROR = 8001
    PARSER_NOT_SUPPORTED = 8002
    CHUNKING_FAILED = 8003
    FILE_TOO_LARGE = 8004

    # mem-knowledge (9xxx, reserved for the KB plan)
    INDEX_BUILD_FAILED = 9001
    EMBEDDING_FAILED = 9002
    SEARCH_FAILED = 9003
    METADATA_FIELD_NOT_FOUND = 9100
    METADATA_INVALID_OPERATOR = 9101
    METADATA_INVALID_VALUE_TYPE = 9102
    METADATA_BUILTIN_NOT_ENABLED = 9103
    METADATA_CROSS_KB_BATCH = 9104

    # memory (95xx/96xx, reserved for the memory plan)
    MEMORY_WRITE_FAILED = 9501
    MEMORY_READ_FAILED = 9502
    MEMORY_CONFIG_NOT_FOUND = 9503
    INVALID_USER_ID = 9601
    INSUFFICIENT_DATA = 9602
    INVALID_FILTER_PARAMS = 9603
    ANALYSIS_FAILED = 9604
    PROFILE_STORAGE_ERROR = 9605

    # shared system (10xxx)
    INTERNAL_ERROR = 10001
    DB_ERROR = 10002
    SERVICE_UNAVAILABLE = 10003
    RATE_LIMITED = 10004


# Wire-frozen legacy values, kept for client compatibility: FAILED login /
# user lookup returns HTTP 200 with a non-zero code, and *NOT_FOUND business
# errors map to 400 (not 404). Do not "fix" these entries.
HTTP_MAPPING: dict[BizCode, int] = {
    BizCode.OK: 200,
    BizCode.BAD_REQUEST: 400,
    BizCode.VALIDATION_FAILED: 400,
    BizCode.MISSING_PARAMETER: 400,
    BizCode.INVALID_PARAMETER: 400,
    BizCode.CONTENT_BLOCKED: 400,
    BizCode.UNAUTHORIZED: 401,
    BizCode.TOKEN_INVALID: 401,
    BizCode.TOKEN_EXPIRED: 401,
    BizCode.TOKEN_BLACKLISTED: 401,
    BizCode.PASSWORD_ERROR: 400,
    BizCode.LOGIN_FAILED: 200,
    BizCode.FORBIDDEN: 403,
    BizCode.TENANT_NOT_FOUND: 400,
    BizCode.WORKSPACE_NO_ACCESS: 403,
    BizCode.WORKSPACE_INVITE_NOT_FOUND: 400,
    BizCode.WORKSPACE_ACCESS_DENIED: 403,
    BizCode.API_KEY_NOT_FOUND: 400,
    BizCode.API_KEY_DUPLICATE_NAME: 400,
    BizCode.API_KEY_INVALID: 401,
    BizCode.API_KEY_EXPIRED: 401,
    BizCode.API_KEY_INACTIVE: 401,
    BizCode.API_KEY_INVALID_SCOPE: 403,
    BizCode.API_KEY_INVALID_RESOURCE: 403,
    BizCode.API_KEY_QPS_LIMIT_EXCEEDED: 429,
    BizCode.API_KEY_DAILY_LIMIT_EXCEEDED: 429,
    BizCode.API_KEY_QUOTA_EXCEEDED: 429,
    BizCode.API_KEY_RATE_LIMIT_EXCEEDED: 429,
    BizCode.QUOTA_EXCEEDED: 402,
    BizCode.RATE_LIMIT_EXCEEDED: 429,
    BizCode.NOT_FOUND: 400,
    BizCode.USER_NOT_FOUND: 200,
    BizCode.WORKSPACE_NOT_FOUND: 400,
    BizCode.MODEL_NOT_FOUND: 400,
    BizCode.KNOWLEDGE_NOT_FOUND: 400,
    BizCode.DOCUMENT_NOT_FOUND: 400,
    BizCode.FILE_NOT_FOUND: 400,
    BizCode.APP_NOT_FOUND: 400,
    BizCode.RELEASE_NOT_FOUND: 400,
    BizCode.USER_NO_ACCESS: 401,
    BizCode.MODEL_DEPRECATED: 400,
    BizCode.CHANNEL_DISABLED: 409,
    BizCode.NO_AVAILABLE_CHANNEL: 409,
    BizCode.SPEEDBEAR_CHANNEL_MISSING: 400,
    BizCode.CREDENTIAL_DECRYPT_ERROR: 500,
    BizCode.MODEL_AVAILABLE_IN_PLAZA: 400,
    BizCode.DUPLICATE_NAME: 409,
    BizCode.RESOURCE_ALREADY_EXISTS: 409,
    BizCode.VERSION_ALREADY_EXISTS: 409,
    BizCode.STATE_CONFLICT: 409,
    BizCode.RESOURCE_IN_USE: 409,
    BizCode.VERSION_LIMIT_EXCEEDED: 409,
    BizCode.PUBLISH_FAILED: 500,
    BizCode.NO_DRAFT_TO_PUBLISH: 400,
    BizCode.ROLLBACK_TARGET_NOT_FOUND: 400,
    BizCode.APP_TYPE_NOT_SUPPORTED: 400,
    BizCode.AGENT_CONFIG_MISSING: 400,
    BizCode.SHARE_DISABLED: 403,
    BizCode.INVALID_PASSWORD: 401,
    BizCode.PASSWORD_REQUIRED: 401,
    BizCode.EMBED_NOT_ALLOWED: 403,
    BizCode.PERMISSION_DENIED: 403,
    BizCode.INVALID_CONVERSATION: 400,
    BizCode.CONFIG_MISSING: 400,
    BizCode.APP_NOT_PUBLISHED: 400,
    BizCode.MODEL_NOT_CONFIGURED: 400,
    BizCode.TOOL_NOT_FOUND: 404,
    BizCode.FEATURE_NOT_AVAILABLE: 403,
    BizCode.EXTERNAL_CATALOG_UNAVAILABLE: 503,
    BizCode.EXTERNAL_CATALOG_NOT_FOUND: 404,
    BizCode.EXTERNAL_CATALOG_CONFLICT: 409,
    BizCode.EXTERNAL_CATALOG_INVALID: 422,
    BizCode.TOOL_OPERATION_INVALID: 422,
    BizCode.TOOL_SELECTION_INACTIVE: 422,
    BizCode.TOOL_METHODS_UNAVAILABLE: 422,
    BizCode.REVIEW_STATE_CONFLICT: 409,
    BizCode.MODEL_CONFIG_INVALID: 400,
    BizCode.API_KEY_MISSING: 400,
    BizCode.PROVIDER_NOT_SUPPORTED: 400,
    BizCode.LLM_ERROR: 500,
    BizCode.EMBEDDING_ERROR: 500,
    BizCode.UPSTREAM_UNKNOWN: 502,
    BizCode.FILE_READ_ERROR: 500,
    BizCode.PARSER_NOT_SUPPORTED: 400,
    BizCode.CHUNKING_FAILED: 500,
    BizCode.FILE_TOO_LARGE: 413,
    BizCode.INDEX_BUILD_FAILED: 500,
    BizCode.EMBEDDING_FAILED: 500,
    BizCode.SEARCH_FAILED: 500,
    BizCode.METADATA_FIELD_NOT_FOUND: 400,
    BizCode.METADATA_INVALID_OPERATOR: 400,
    BizCode.METADATA_INVALID_VALUE_TYPE: 400,
    BizCode.METADATA_BUILTIN_NOT_ENABLED: 400,
    BizCode.METADATA_CROSS_KB_BATCH: 400,
    BizCode.MEMORY_WRITE_FAILED: 500,
    BizCode.MEMORY_READ_FAILED: 500,
    BizCode.MEMORY_CONFIG_NOT_FOUND: 400,
    BizCode.INVALID_USER_ID: 400,
    BizCode.INSUFFICIENT_DATA: 400,
    BizCode.INVALID_FILTER_PARAMS: 400,
    BizCode.ANALYSIS_FAILED: 500,
    BizCode.PROFILE_STORAGE_ERROR: 500,
    BizCode.INTERNAL_ERROR: 500,
    BizCode.DB_ERROR: 500,
    BizCode.SERVICE_UNAVAILABLE: 503,
    BizCode.RATE_LIMITED: 429,
}

# Reverse direction for raw HTTP exceptions raised by the framework or route
# bodies. ``HTTP_MAPPING`` cannot be inverted: it is many-to-one and keeps
# wire-frozen legacy values (for example NOT_FOUND -> 400).
HTTP_STATUS_TO_BIZ_CODE: dict[int, BizCode] = {
    400: BizCode.BAD_REQUEST,
    401: BizCode.UNAUTHORIZED,
    402: BizCode.QUOTA_EXCEEDED,
    403: BizCode.FORBIDDEN,
    404: BizCode.NOT_FOUND,
    405: BizCode.BAD_REQUEST,
    409: BizCode.STATE_CONFLICT,
    413: BizCode.BAD_REQUEST,
    422: BizCode.VALIDATION_FAILED,
    429: BizCode.RATE_LIMIT_EXCEEDED,
    500: BizCode.INTERNAL_ERROR,
    502: BizCode.UPSTREAM_UNKNOWN,
    503: BizCode.SERVICE_UNAVAILABLE,
    504: BizCode.SERVICE_UNAVAILABLE,
}

ERROR_CODE_TO_BIZ_CODE: dict[str, BizCode] = {
    "QUOTA_EXCEEDED": BizCode.QUOTA_EXCEEDED,
    "RATE_LIMIT_EXCEEDED": BizCode.RATE_LIMIT_EXCEEDED,
    "API_KEY_NOT_FOUND": BizCode.API_KEY_NOT_FOUND,
    "API_KEY_INVALID": BizCode.API_KEY_INVALID,
    "API_KEY_EXPIRED": BizCode.API_KEY_EXPIRED,
    "WORKSPACE_NOT_FOUND": BizCode.WORKSPACE_NOT_FOUND,
    "WORKSPACE_NO_ACCESS": BizCode.WORKSPACE_NO_ACCESS,
    "PERMISSION_DENIED": BizCode.PERMISSION_DENIED,
    "TOKEN_EXPIRED": BizCode.TOKEN_EXPIRED,
    "TOKEN_INVALID": BizCode.TOKEN_INVALID,
    "VALIDATION_FAILED": BizCode.VALIDATION_FAILED,
    "INVALID_PARAMETER": BizCode.INVALID_PARAMETER,
    "MISSING_PARAMETER": BizCode.MISSING_PARAMETER,
}


def is_known_code(value: int | str) -> bool:
    numeric = coerce_code(value)
    return numeric is not None and numeric in BizCode._value2member_map_


def coerce_code(value: BizCode | int | str | None) -> int | None:
    """Return a numeric code, preserving unknown numeric codes.

    Known enum names and digit strings are accepted; unknown names return
    ``None`` (the caller decides between pass-through and fallback).
    """

    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, BizCode):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            return int(stripped)
        mapped = ERROR_CODE_TO_BIZ_CODE.get(stripped)
        if mapped is not None:
            return int(mapped)
        try:
            return int(BizCode[stripped])
        except KeyError:
            return None
    return None


def as_biz_code(value: BizCode | int | str | None) -> BizCode:
    """Legacy coercion: unknown values collapse to ``BAD_REQUEST``."""

    numeric = coerce_code(value)
    if numeric is None:
        return BizCode.BAD_REQUEST
    try:
        return BizCode(numeric)
    except ValueError:
        return BizCode.BAD_REQUEST


def http_status_for(value: BizCode | int | str | None) -> int:
    """Known codes use the table; unknown 5-digit codes are server errors."""

    numeric = coerce_code(value)
    if numeric is None:
        return 400
    try:
        member = BizCode(numeric)
    except ValueError:
        return 500 if numeric >= 10000 else 400
    return HTTP_MAPPING.get(member, 500 if numeric >= 10000 else 400)


def code_name(value: BizCode | int | str | None) -> str:
    numeric = coerce_code(value)
    if numeric is None:
        return str(value) if value is not None else "UNKNOWN"
    try:
        return BizCode(numeric).name
    except ValueError:
        return str(numeric)


__all__ = [
    "ERROR_CODE_TO_BIZ_CODE",
    "HTTP_MAPPING",
    "HTTP_STATUS_TO_BIZ_CODE",
    "BizCode",
    "as_biz_code",
    "code_name",
    "coerce_code",
    "http_status_for",
    "is_known_code",
]
