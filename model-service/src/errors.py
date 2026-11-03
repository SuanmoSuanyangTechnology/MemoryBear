"""Model domain error vocabulary: BizCode, HTTP mapping, business exceptions.

Wire-compatible port of the host ``app/core/error_codes.py`` and
``app/core/exceptions.py``. ``BizCode`` values MUST stay identical to the host
enum — the legacy envelope carries the numeric code straight to clients, and
the proxy passes the service response through unchanged.
"""

from __future__ import annotations

import time
from typing import Any

from error_core.codes import (
    ERROR_CODE_TO_BIZ_CODE,
    HTTP_MAPPING,
    BizCode,
    as_biz_code,
    http_status_for,
)
from fastapi import HTTPException

from .i18n import get_current_locale, get_default_locale, translate
from .sensitive import SensitiveDataFilter


class BusinessException(Exception):
    """业务逻辑异常基类"""

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
        # 资源显示名先翻译，再作为参数参与模板渲染（与宿主同序）
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


def filter_business_message(exc: BusinessException) -> tuple[str, dict[str, Any]]:
    """Mask credentials the same way the host handler does before rendering."""

    return SensitiveDataFilter.filter_message(exc.message, exc.context)


__all__ = [
    "BizCode",
    "BusinessException",
    "ERROR_CODE_TO_BIZ_CODE",
    "HTTP_MAPPING",
    "I18nException",
    "InternalServerError",
    "QuotaExceededError",
    "as_biz_code",
    "filter_business_message",
    "http_status_for",
]
