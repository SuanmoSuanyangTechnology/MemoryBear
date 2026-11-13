"""Application domain error vocabulary: BizCode, HTTP mapping, business exceptions.

Wire-compatible port of the host ``app/core/error_codes.py`` and
``app/core/exceptions.py``. ``BizCode`` values MUST stay identical to the host
enum — the legacy envelope carries the numeric code straight to clients, and
the gateway passes the service response through unchanged.
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


class BusinessException(Exception):
    """Base class for business-logic errors."""

    def __init__(
        self,
        message: str,
        code: BizCode | int | None = None,
        context: dict[str, Any] | None = None,
        cause: Exception | None = None,
        data: dict[str, Any] | None = None,
    ):
        self.message = message
        self.code = code if code is not None else BizCode.BAD_REQUEST
        self.context = dict(context) if context else {}
        self.cause = cause
        self.data = dict(data) if data else None
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
    """Port of the host ``ResourceNotFoundException`` (message shape unchanged).

    ``code`` defaults to the legacy ``FILE_NOT_FOUND`` wire value; resource types
    with a dedicated domain code (currently 应用 → ``APP_NOT_FOUND``) pass it.
    """

    def __init__(
        self,
        resource_type: str,
        resource_id: str | None = None,
        *,
        code: BizCode | int | None = None,
        **kwargs: Any,
    ):
        message = f"{resource_type} 不存在"
        context: dict[str, Any] = {"resource_type": resource_type}
        if resource_id:
            context["resource_id"] = resource_id
        if "context" in kwargs:
            context.update(kwargs.pop("context"))
        super().__init__(
            message,
            code if code is not None else BizCode.FILE_NOT_FOUND,
            context,
            **kwargs,
        )


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


class SubAgentValidationError(I18nException):
    """Cluster save rejected because at least one sub-agent is unusable (1003).

    ``BizCode.INVALID_PARAMETER`` alone would render as the generic copy
    "请求参数无效", which tells the user nothing about *which* member is wrong.
    Carrying the collected reasons in the message keeps the wire code unchanged
    while making the failure actionable.
    """

    def __init__(self, details: str, **params: Any):
        super().__init__(
            error_key="errors.api.sub_agent_invalid",
            status_code=400,
            error_code="INVALID_PARAMETER",
            details=details,
            **params,
        )


class SubAgentUnpublishedError(I18nException):
    """Cluster publish pre-scan found unpublished sub-agents (wire code 1003).

    The code is deliberately ``INVALID_PARAMETER`` rather than
    ``APP_NOT_PUBLISHED``: see ``SubAgentValidationError`` — a domain code with
    generic copy hides the member list the user needs to fix it.
    """

    def __init__(self, details: str, **params: Any):
        super().__init__(
            error_key="errors.api.sub_agent_unpublished",
            status_code=400,
            error_code="INVALID_PARAMETER",
            details=details,
            **params,
        )


class WorkflowAgentUnpublishedError(I18nException):
    """Workflow save rejected because an ``agent`` node references an unusable Agent.

    A workflow copied out of the resource center takes its referenced Agents along
    as *drafts* (``asset_service._remap_workflow_nodes``), so the node points at an
    app that has no release yet. Publishing already refuses that
    (``_pin_workflow_agent_references``), but only after the user has finished
    editing; the cluster config does the same check on save, and the workflow side
    should not be looser.

    Wire code stays ``INVALID_PARAMETER`` for the same reason as
    ``SubAgentValidationError``: the enumerated node/app names must survive to the
    user, and the generic "请求参数无效" copy would swallow them.
    """

    def __init__(self, details: str, **params: Any):
        super().__init__(
            error_key="errors.api.workflow_agent_unpublished",
            status_code=400,
            error_code="INVALID_PARAMETER",
            details=details,
            **params,
        )


class ShareDependencyUnavailableError(I18nException):
    """Sharing rejected because the snapshot references unusable dependencies (1003).

    Same reasoning as ``SubAgentValidationError``: the wire code stays
    ``INVALID_PARAMETER``, but the copy must name *which* tool / skill / agent
    version is broken and why — the generic "请求参数无效" leaves the user with
    nothing to fix. ``data`` stays ``{}`` on the wire (unified error contract);
    the itemised list travels inside ``msg``.
    """

    def __init__(self, details: str, **params: Any):
        super().__init__(
            error_key="errors.api.share_dependencies_unavailable",
            status_code=400,
            error_code="INVALID_PARAMETER",
            details=details,
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
    "ShareDependencyUnavailableError",
    "SubAgentUnpublishedError",
    "SubAgentValidationError",
    "ValidationException",
    "WorkflowAgentUnpublishedError",
    "as_biz_code",
    "http_status_for",
]
