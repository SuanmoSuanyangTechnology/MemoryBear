"""Service exceptions and business codes."""

from __future__ import annotations


class BizCode:
    VALIDATION_FAILED = 1001
    MISSING_PARAMETER = 1002
    INVALID_PARAMETER = 1003
    UNAUTHORIZED = 2001
    WORKSPACE_ACCESS_DENIED = 3005
    USER_NOT_FOUND = 4001
    INTERNAL_ERROR = 10001
    SERVICE_UNAVAILABLE = 10003


class ProfileError(Exception):
    def __init__(
        self,
        code: int,
        message: str,
        *,
        detail: str = "",
        status_code: int = 400,
        data: object = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail
        self.status_code = status_code
        self.data = data
