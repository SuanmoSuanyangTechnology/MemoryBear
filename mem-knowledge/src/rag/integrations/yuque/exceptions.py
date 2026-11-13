"""Yuque integration errors."""


class YuqueError(Exception):
    def __init__(self, message: str, error_code: str | None = None, *, response=None):
        super().__init__(message)
        self.error_code = error_code
        self.response = response


class YuqueAuthError(YuqueError):
    pass


class YuqueAPIError(YuqueError):
    pass


__all__ = ["YuqueAPIError", "YuqueAuthError", "YuqueError"]
