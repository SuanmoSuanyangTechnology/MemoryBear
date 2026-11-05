"""External catalog errors carrying structured detail for the legacy envelope."""

from __future__ import annotations

from typing import Any

from ..errors import BizCode, BusinessException


class ExternalCatalogError(BusinessException):
    def __init__(
        self,
        code: BizCode,
        message: str,
        *,
        data: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, code, context, data=data)
