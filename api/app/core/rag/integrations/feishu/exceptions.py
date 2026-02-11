"""Exception classes for Feishu integration."""


class FeishuError(Exception):
    """Base exception for all Feishu-related errors."""
    
    def __init__(self, message: str, error_code: str = None, details: dict = None):
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.details = details or {}


class FeishuAuthError(FeishuError):
    """Authentication error with Feishu API."""
    pass


class FeishuAPIError(FeishuError):
    """General API error from Feishu."""
    pass


class FeishuNotFoundError(FeishuError):
    """Resource not found error (404)."""
    pass


class FeishuPermissionError(FeishuError):
    """Permission denied error (403)."""
    pass


class FeishuRateLimitError(FeishuError):
    """Rate limit exceeded error (429)."""
    pass


class FeishuNetworkError(FeishuError):
    """Network-related error (timeout, connection failure)."""
    pass


class FeishuDataError(FeishuError):
    """Data parsing or validation error."""
    pass
