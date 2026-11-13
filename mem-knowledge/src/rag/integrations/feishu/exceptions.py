"""Feishu integration errors."""


class FeishuError(Exception):
    def __init__(self, message: str, error_code: str | None = None, *, response=None):
        super().__init__(message)
        self.error_code = error_code
        self.response = response


class FeishuAuthError(FeishuError):
    pass


class FeishuAPIError(FeishuError):
    pass


class FeishuFolderNotFoundError(FeishuAPIError):
    def __init__(self, error_code: str, *, folder_token: str, response):
        super().__init__("Feishu folder not found", error_code, response=response)
        self.folder_token = folder_token


__all__ = ["FeishuAPIError", "FeishuAuthError", "FeishuError"]
