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


class LocalizedProfileError(ProfileError):
    """A ``ProfileError`` whose message and detail are translation keys.

    The service layer raises this without knowing the caller's language; the
    global exception handler resolves the keys against the request language.
    """

    def __init__(
        self,
        code: int,
        message_key: str,
        *,
        message_params: dict[str, object] | None = None,
        detail: str = "",
        detail_key: str | None = None,
        detail_params: dict[str, object] | None = None,
        status_code: int = 400,
        data: object = None,
    ) -> None:
        self.message_key = message_key
        self.message_params = dict(message_params or {})
        self.detail_key = detail_key
        self.detail_params = dict(detail_params or {})
        super().__init__(
            code,
            message_key,
            detail=detail or detail_key or "",
            status_code=status_code,
            data=data,
        )
