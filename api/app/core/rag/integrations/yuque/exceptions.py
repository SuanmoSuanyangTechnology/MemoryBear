"""Exception classes for Yuque integration."""


class YuqueError(Exception):
    """Base exception for all Yuque-related errors."""
    
    def __init__(self, message: str, error_code: str = None, details: dict = None):
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.details = details or {}


class YuqueAuthError(YuqueError):
    """Authentication error with Yuque API."""
    pass


class YuqueAPIError(YuqueError):
    """General API error from Yuque."""
    pass


class YuqueNotFoundError(YuqueError):
    """Resource not found error (404)."""
    pass


class YuquePermissionError(YuqueError):
    """Permission denied error (403)."""
    pass


class YuqueRateLimitError(YuqueError):
    """Rate limit exceeded error (429)."""
    pass


class YuqueNetworkError(YuqueError):
    """Network-related error (timeout, connection failure)."""
    pass


class YuqueDataError(YuqueError):
    """Data parsing or validation error."""
    pass
