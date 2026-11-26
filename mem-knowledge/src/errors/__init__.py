"""知识库公共错误契约。"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from types import MappingProxyType

from .definitions import (
    ERROR_DEFINITIONS,
    MAX_PUBLIC_PARAM_LENGTH,
    TRANSIENT_DEPENDENCY_HTTP_STATUSES,
    ErrorCode,
    ErrorDefinition,
    ErrorResponseStyle,
    PublicScalar,
    public_text,
    valid_params,
)

logger = logging.getLogger(__name__)


# Celery 会将异常模块名写入任务结果，保留 src.errors.KnowledgeError 供滚动发布期间识别。
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
        response_code: int | None = None,
        http_status_override: int | None = None,
        upstream_response_code: int | None = None,
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
        self._response_code = response_code
        # 仅在 HTTP 出口生效，gRPC trailer 与任务仍使用原状态。
        self.http_status_override = http_status_override
        self.upstream_response_code = upstream_response_code

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
        return (
            self._status_code
            if self._status_code is not None
            else ERROR_DEFINITIONS[self.code].status_code
        )

    @property
    def grpc_status(self) -> str:
        return ERROR_DEFINITIONS[self.code].grpc_status

    @property
    def response_code(self) -> int:
        return (
            self._response_code
            if self._response_code is not None
            else ERROR_DEFINITIONS[self.code].response_code
        )

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
        response_code: int | None = None,
        http_status_override: int | None = None,
        upstream_response_code: int | None = None,
    ) -> KnowledgeError:
        return cls(
            code=code,
            message=message,
            params=params,
            status_code=status_code,
            diagnostic=diagnostic,
            response_code=response_code,
            http_status_override=http_status_override,
            upstream_response_code=upstream_response_code,
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
