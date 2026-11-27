"""Management-plane error vocabulary (wire-identical to the monolith).

Codes live in ``error_core``; these classes keep the host's constructors,
default messages and codes so migrated routes raise exactly what the monolith
raised during cutover.
"""

from __future__ import annotations

import time
from typing import Any

from error_core.codes import ERROR_CODE_TO_BIZ_CODE, BizCode
from fastapi import HTTPException

from src.i18n import get_current_locale, get_default_locale, translate


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


class PermissionDeniedException(BusinessException):
    """Permission denied (403); rendered as its raw message, not catalog copy."""

    def __init__(self, message: str = "权限不足", **kwargs: Any):
        super().__init__(message, BizCode.FORBIDDEN, **kwargs)


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

    @staticmethod
    def _generate_error_code(error_key: str) -> str:
        if error_key.startswith("errors."):
            error_key = error_key[7:]
        return "_".join(error_key.split(".")).upper()


class QuotaExceededError(I18nException):
    """Quota exceeded (402)."""

    _RESOURCE_KEY_MAP = {
        "workspace": "errors.quota_resources.workspace",
        "app": "errors.quota_resources.app",
        "skill": "errors.quota_resources.skill",
        "knowledge_capacity": "errors.quota_resources.knowledge_capacity",
        "memory_engine": "errors.quota_resources.memory_engine",
        "end_user": "errors.quota_resources.end_user",
        "model": "errors.quota_resources.model",
        "ontology_project": "errors.quota_resources.ontology_project",
        "api_ops_rate_limit": "errors.quota_resources.api_ops_rate_limit",
    }

    def __init__(self, resource: str | None = None, **params: Any):
        # Translate the resource display name first, then render the template.
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
    """Internal server error (500)."""

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


class ServiceUnavailableError(I18nException):
    """Service unavailable (503)."""

    def __init__(
        self,
        error_key: str = "errors.common.service_unavailable",
        error_code: str | None = None,
        **params: Any,
    ):
        super().__init__(
            error_key=error_key,
            status_code=503,
            error_code=error_code,
            **params,
        )


__all__ = [
    "BusinessException",
    "I18nException",
    "InternalServerError",
    "PermissionDeniedException",
    "QuotaExceededError",
    "ServiceUnavailableError",
]
