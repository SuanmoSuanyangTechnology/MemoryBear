"""Business error vocabulary; codes come from the shared error-core package."""
from __future__ import annotations

from typing import Any

from error_core.codes import BizCode


class BusinessException(Exception):
    def __init__(
        self,
        message: str,
        code: BizCode | int | None = None,
        status_code: int | None = None,
        context: dict[str, Any] | None = None,
    ):
        self.message = message
        self.code = code if code is not None else BizCode.BAD_REQUEST
        self.status_code = status_code
        self.context = dict(context) if context else {}
        super().__init__(message)
