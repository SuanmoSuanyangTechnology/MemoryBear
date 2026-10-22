"""Application domain error vocabulary: BizCode, HTTP mapping, business exceptions.

Wire-compatible port of the host ``app/core/error_codes.py`` and
``app/core/exceptions.py``. ``BizCode`` values MUST stay identical to the host
enum — the legacy envelope carries the numeric code straight to clients, and
the gateway passes the service response through unchanged.
"""

from __future__ import annotations

import time
from enum import IntEnum
from typing import Any

from fastapi import HTTPException

from .i18n import get_current_locale, get_default_locale, translate


class BizCode(IntEnum):
    """Subset of the host BizCode covering the application domain and shared errors."""

    # generic (1xxx)
    OK = 0
    BAD_REQUEST = 1000
    VALIDATION_FAILED = 1001
    MISSING_PARAMETER = 1002
    INVALID_PARAMETER = 1003
    # authentication / authorization (2xxx/3xxx)
    UNAUTHORIZED = 2001
    TOKEN_INVALID = 2002
    TOKEN_EXPIRED = 2003
    TOKEN_BLACKLISTED = 2004
    FORBIDDEN = 3001
    WORKSPACE_NO_ACCESS = 3003
    WORKSPACE_ACCESS_DENIED = 3005
    # API key / quota (3xxx)
    API_KEY_NOT_FOUND = 3007
    API_KEY_INVALID = 3009
    API_KEY_EXPIRED = 3010
    API_KEY_INVALID_SCOPE = 3012
    QUOTA_EXCEEDED = 3018
    RATE_LIMIT_EXCEEDED = 3019
    # resources (4xxx)
    NOT_FOUND = 4000
    USER_NOT_FOUND = 4001
    WORKSPACE_NOT_FOUND = 4002
    MODEL_NOT_FOUND = 4003
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
    # application publishing (6xxx, the application domain's own codes)
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
    # model (7xxx, kept for host-enum parity; consumed by the model domain)
    MODEL_CONFIG_INVALID = 7001
    API_KEY_MISSING = 7002
    PROVIDER_NOT_SUPPORTED = 7003
    LLM_ERROR = 7004
    EMBEDDING_ERROR = 7005
    # parsers (8xxx, host code raised by the prompt-optimizer variable parser)
    PARSER_NOT_SUPPORTED = 8002
    # memory (95xx, host codes consumed by the end-user create path)
    MEMORY_CONFIG_NOT_FOUND = 9503
    INVALID_USER_ID = 9601
    # system (100xx)
    INTERNAL_ERROR = 10001
    DB_ERROR = 10002
    SERVICE_UNAVAILABLE = 10003
    RATE_LIMITED = 10004


# Suggested HTTP status mapping (same values as the host HTTP_MAPPING; codes not
# listed fall back to 400).
HTTP_MAPPING: dict[BizCode, int] = {
    BizCode.OK: 200,
    BizCode.BAD_REQUEST: 400,
    BizCode.VALIDATION_FAILED: 400,
    BizCode.MISSING_PARAMETER: 400,
    BizCode.INVALID_PARAMETER: 400,
    BizCode.UNAUTHORIZED: 401,
    BizCode.TOKEN_INVALID: 401,
    BizCode.TOKEN_EXPIRED: 401,
    BizCode.TOKEN_BLACKLISTED: 401,
    BizCode.FORBIDDEN: 403,
    BizCode.WORKSPACE_NO_ACCESS: 403,
    BizCode.WORKSPACE_ACCESS_DENIED: 403,
    BizCode.NOT_FOUND: 400,
    BizCode.USER_NOT_FOUND: 200,
    BizCode.USER_NO_ACCESS: 401,
    BizCode.WORKSPACE_NOT_FOUND: 400,
    BizCode.MODEL_NOT_FOUND: 400,
    BizCode.MODEL_DEPRECATED: 400,
    BizCode.MODEL_AVAILABLE_IN_PLAZA: 400,
    BizCode.CHANNEL_DISABLED: 409,
    BizCode.NO_AVAILABLE_CHANNEL: 409,
    BizCode.SPEEDBEAR_CHANNEL_MISSING: 400,
    BizCode.CREDENTIAL_DECRYPT_ERROR: 500,
    BizCode.FILE_NOT_FOUND: 400,
    BizCode.APP_NOT_FOUND: 400,
    BizCode.RELEASE_NOT_FOUND: 400,
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
    BizCode.API_KEY_NOT_FOUND: 400,
    BizCode.API_KEY_INVALID: 401,
    BizCode.API_KEY_EXPIRED: 401,
    BizCode.API_KEY_INVALID_SCOPE: 403,
    BizCode.QUOTA_EXCEEDED: 402,
    BizCode.MODEL_CONFIG_INVALID: 400,
    BizCode.API_KEY_MISSING: 400,
    BizCode.PROVIDER_NOT_SUPPORTED: 400,
    BizCode.LLM_ERROR: 500,
    BizCode.EMBEDDING_ERROR: 500,
    BizCode.PARSER_NOT_SUPPORTED: 400,
    BizCode.MEMORY_CONFIG_NOT_FOUND: 400,
    BizCode.INVALID_USER_ID: 400,
    BizCode.INTERNAL_ERROR: 500,
    BizCode.DB_ERROR: 500,
    BizCode.SERVICE_UNAVAILABLE: 503,
    BizCode.RATE_LIMITED: 429,
    BizCode.RATE_LIMIT_EXCEEDED: 429,
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


def as_biz_code(value: BizCode | int | None) -> BizCode:
    """Coerce an exception code to a BizCode exactly like the host handler."""

    if isinstance(value, BizCode):
        return value
    if isinstance(value, int):
        try:
            return BizCode(value)
        except ValueError:
            return BizCode.BAD_REQUEST
    return BizCode.BAD_REQUEST


def http_status_for(value: BizCode | int | None) -> int:
    return HTTP_MAPPING.get(as_biz_code(value), 400)


class BusinessException(Exception):
    """Base class for business-logic errors."""

    def __init__(
        self,
        message: str,
        code: BizCode | int | None = None,
        context: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ):
        self.message = message
        self.code = code if code is not None else BizCode.BAD_REQUEST
        self.context = dict(context) if context else {}
        self.cause = cause
        super().__init__(self.message)

    def __str__(self) -> str:
        ctx = f", context={self.context}" if self.context else ""
        code_name = self.code.name if isinstance(self.code, BizCode) else str(self.code)
        return f"{code_name}: {self.message}{ctx}"


class I18nException(HTTPException):
    """Translated error carrying the host's detail payload inside HTTPException."""

    def __init__(
        self,
        error_key: str,
        status_code: int = 400,
        error_code: str | None = None,
        locale: str | None = None,
        headers: dict[str, str] | None = None,
        **params: Any,
    ):
        self.error_key = error_key
        self.error_code = error_code or self._generate_error_code(error_key)
        self.params = params

        language = locale or get_current_locale() or get_default_locale()
        message = translate(error_key, language, **params)

        biz_code = ERROR_CODE_TO_BIZ_CODE.get(self.error_code, BizCode.BAD_REQUEST)
        detail = {
            "code": biz_code.value,
            "msg": message,
            "message": message,
            "error_code": self.error_code,
            "data": params if params else {},
            "error": message,
            "time": int(time.time() * 1000),
        }
        super().__init__(status_code=status_code, detail=detail, headers=headers)

    def _generate_error_code(self, error_key: str) -> str:
        if error_key.startswith("errors."):
            error_key = error_key[7:]
        return "_".join(error_key.split(".")).upper()


class ResourceNotFoundException(BusinessException):
    """Port of the host ``ResourceNotFoundException`` (message shape and 4006
    code must stay identical on the wire)."""

    def __init__(self, resource_type: str, resource_id: str | None = None, **kwargs: Any):
        message = f"{resource_type} 不存在"
        context: dict[str, Any] = {"resource_type": resource_type}
        if resource_id:
            context["resource_id"] = resource_id
        if "context" in kwargs:
            context.update(kwargs.pop("context"))
        super().__init__(message, BizCode.FILE_NOT_FOUND, context, **kwargs)


class ValidationException(BusinessException):
    """Port of the host validation error (1001) carrying an optional field name."""

    def __init__(self, message: str, field: str | None = None, **kwargs: Any):
        context: dict[str, Any] = {"field": field} if field else {}
        if "context" in kwargs:
            context.update(kwargs.pop("context"))
        super().__init__(message, BizCode.VALIDATION_FAILED, context, **kwargs)


class DuplicateResourceException(BusinessException):
    """Port of the host duplicate-resource error (5001)."""

    def __init__(self, message: str = "资源已存在", **kwargs: Any):
        super().__init__(message, BizCode.DUPLICATE_NAME, **kwargs)


class QuotaExceededError(I18nException):
    """Quota exceeded error (402)."""

    _RESOURCE_KEY_MAP = {
        "workspace": "errors.quota_resources.workspace",
        "app": "errors.quota_resources.app",
        "skill": "errors.quota_resources.skill",
        "knowledge_capacity": "errors.quota_resources.knowledge_capacity",
        "memory_engine": "errors.quota_resources.memory_engine",
        "end_user": "errors.quota_resources.end_user",
        "model": "errors.quota_resources.model",
        "ontology_project": "errors.quota_resources.ontology_project",
    }

    def __init__(self, resource: str | None = None, **params: Any):
        # Translate the resource display name first, then pass it into template
        # rendering (same order as the host).
        if resource:
            resource_i18n_key = self._RESOURCE_KEY_MAP.get(resource)
            if resource_i18n_key:
                locale = get_current_locale() or get_default_locale()
                params["resource"] = translate(resource_i18n_key, locale)
            else:
                params["resource"] = resource
        super().__init__(
            error_key="errors.api.quota_exceeded",
            status_code=402,
            error_code="QUOTA_EXCEEDED",
            **params,
        )


class InternalServerError(I18nException):
    """Internal server error (500); host parity keeps code 1000 on the wire."""

    def __init__(
        self,
        error_key: str = "errors.common.internal_error",
        error_code: str | None = None,
        **params: Any,
    ):
        super().__init__(
            error_key=error_key,
            status_code=500,
            error_code=error_code,
            **params,
        )


__all__ = [
    "BizCode",
    "BusinessException",
    "DuplicateResourceException",
    "ERROR_CODE_TO_BIZ_CODE",
    "HTTP_MAPPING",
    "I18nException",
    "InternalServerError",
    "QuotaExceededError",
    "ResourceNotFoundException",
    "ValidationException",
    "as_biz_code",
    "http_status_for",
]
