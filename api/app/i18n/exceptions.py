"""
Internationalized exception classes for i18n system.

This module provides exception classes that automatically translate
error messages based on the current request's language.
"""

import logging
from contextvars import ContextVar
from typing import Any, Dict, Optional

from fastapi import HTTPException, Request

from app.i18n.service import get_translation_service

logger = logging.getLogger(__name__)

# Context variable to store current locale
_current_locale: ContextVar[Optional[str]] = ContextVar("current_locale", default=None)


def set_current_locale(locale: str) -> None:
    """
    Set the current locale in the context variable.
    
    This should be called by the LanguageMiddleware.
    
    Args:
        locale: Locale code (e.g., "zh", "en")
    """
    _current_locale.set(locale)


def get_current_locale() -> Optional[str]:
    """
    Get the current locale from the context variable.
    
    Returns:
        Locale code or None if not set
    """
    return _current_locale.get()


class I18nException(HTTPException):
    """
    Base exception class with automatic i18n support.

    This exception automatically translates error messages based on:
    1. The current request's language (from request.state.language)
    2. The fallback language if request language is not available
    3. The error key itself if no translation is found

    Features:
    - Automatic error message translation
    - Parameterized error messages support
    - Consistent error response format
    - Language-aware error handling

    Usage:
        # Simple error
        raise I18nException(
            error_key="errors.workspace.not_found",
            status_code=404
        )

        # Error with parameters
        raise I18nException(
            error_key="errors.validation.missing_field",
            status_code=400,
            field="name"
        )

        # Custom error code
        raise I18nException(
            error_key="errors.workspace.not_found",
            error_code="WORKSPACE_NOT_FOUND",
            status_code=404,
            workspace_id="123"
        )
    """

    def __init__(
        self,
        error_key: str,
        status_code: int = 400,
        error_code: Optional[str] = None,
        locale: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **params
    ):
        """
        Initialize the i18n exception.

        Args:
            error_key: Translation key for the error message
                      (e.g., "errors.workspace.not_found")
            status_code: HTTP status code (default: 400)
            error_code: Custom error code for API clients
                       (default: derived from error_key)
            locale: Target locale for translation (optional)
                   If not provided, uses current request's language
            headers: Additional HTTP headers
            **params: Parameters for parameterized error messages
        """
        self.error_key = error_key
        self.error_code = error_code or self._generate_error_code(error_key)
        self.params = params

        # Get locale from request context if not provided
        if locale is None:
            locale = self._get_current_locale()

        # Translate error message
        translation_service = get_translation_service()
        message = translation_service.translate(
            error_key,
            locale,
            **params
        )

        # Build error detail
        detail = {
            "error_code": self.error_code,
            "message": message,
        }

        # Add parameters to detail if provided
        if params:
            detail["params"] = params

        # Initialize HTTPException
        super().__init__(
            status_code=status_code,
            detail=detail,
            headers=headers
        )

        logger.debug(
            f"I18nException raised: {self.error_code} "
            f"(key: {error_key}, locale: {locale})"
        )

    def _get_current_locale(self) -> str:
        """
        Get the current locale from request context.

        Returns:
            Locale code (e.g., "zh", "en")
        """
        try:
            # Try to get locale from context variable
            locale = _current_locale.get()
            if locale:
                return locale
        except Exception as e:
            logger.debug(f"Could not get locale from context: {e}")

        # Fallback to default locale
        from app.core.config import settings
        return settings.I18N_DEFAULT_LANGUAGE

    def _generate_error_code(self, error_key: str) -> str:
        """
        Generate error code from error key.

        Converts "errors.workspace.not_found" to "WORKSPACE_NOT_FOUND"

        Args:
            error_key: Translation key

        Returns:
            Error code in UPPER_SNAKE_CASE
        """
        # Remove "errors." prefix if present
        if error_key.startswith("errors."):
            error_key = error_key[7:]

        # Convert to UPPER_SNAKE_CASE
        parts = error_key.split(".")
        return "_".join(parts).upper()


# Specific exception classes for common errors

class BadRequestError(I18nException):
    """Bad request error (400)."""

    def __init__(
        self,
        error_key: str = "errors.common.bad_request",
        error_code: Optional[str] = None,
        **params
    ):
        super().__init__(
            error_key=error_key,
            status_code=400,
            error_code=error_code,
            **params
        )


class UnauthorizedError(I18nException):
    """Unauthorized error (401)."""

    def __init__(
        self,
        error_key: str = "errors.auth.unauthorized",
        error_code: Optional[str] = None,
        **params
    ):
        super().__init__(
            error_key=error_key,
            status_code=401,
            error_code=error_code,
            **params
        )


class ForbiddenError(I18nException):
    """Forbidden error (403)."""

    def __init__(
        self,
        error_key: str = "errors.auth.forbidden",
        error_code: Optional[str] = None,
        **params
    ):
        super().__init__(
            error_key=error_key,
            status_code=403,
            error_code=error_code,
            **params
        )


class NotFoundError(I18nException):
    """Not found error (404)."""

    def __init__(
        self,
        error_key: str = "errors.common.not_found",
        error_code: Optional[str] = None,
        **params
    ):
        super().__init__(
            error_key=error_key,
            status_code=404,
            error_code=error_code,
            **params
        )


class ConflictError(I18nException):
    """Conflict error (409)."""

    def __init__(
        self,
        error_key: str = "errors.common.conflict",
        error_code: Optional[str] = None,
        **params
    ):
        super().__init__(
            error_key=error_key,
            status_code=409,
            error_code=error_code,
            **params
        )


class ValidationError(I18nException):
    """Validation error (422)."""

    def __init__(
        self,
        error_key: str = "errors.common.validation_failed",
        error_code: Optional[str] = None,
        **params
    ):
        super().__init__(
            error_key=error_key,
            status_code=422,
            error_code=error_code,
            **params
        )


class InternalServerError(I18nException):
    """Internal server error (500)."""

    def __init__(
        self,
        error_key: str = "errors.common.internal_error",
        error_code: Optional[str] = None,
        **params
    ):
        super().__init__(
            error_key=error_key,
            status_code=500,
            error_code=error_code,
            **params
        )


class ServiceUnavailableError(I18nException):
    """Service unavailable error (503)."""

    def __init__(
        self,
        error_key: str = "errors.common.service_unavailable",
        error_code: Optional[str] = None,
        **params
    ):
        super().__init__(
            error_key=error_key,
            status_code=503,
            error_code=error_code,
            **params
        )


# Domain-specific exception classes

class WorkspaceNotFoundError(NotFoundError):
    """Workspace not found error."""

    def __init__(self, workspace_id: Optional[str] = None, **params):
        if workspace_id:
            params["workspace_id"] = workspace_id
        super().__init__(
            error_key="errors.workspace.not_found",
            error_code="WORKSPACE_NOT_FOUND",
            **params
        )


class WorkspacePermissionDeniedError(ForbiddenError):
    """Workspace permission denied error."""

    def __init__(self, workspace_id: Optional[str] = None, **params):
        if workspace_id:
            params["workspace_id"] = workspace_id
        super().__init__(
            error_key="errors.workspace.permission_denied",
            error_code="WORKSPACE_PERMISSION_DENIED",
            **params
        )


class UserNotFoundError(NotFoundError):
    """User not found error."""

    def __init__(self, user_id: Optional[str] = None, **params):
        if user_id:
            params["user_id"] = user_id
        super().__init__(
            error_key="errors.user.not_found",
            error_code="USER_NOT_FOUND",
            **params
        )


class UserAlreadyExistsError(ConflictError):
    """User already exists error."""

    def __init__(self, identifier: Optional[str] = None, **params):
        if identifier:
            params["identifier"] = identifier
        super().__init__(
            error_key="errors.user.already_exists",
            error_code="USER_ALREADY_EXISTS",
            **params
        )


class TenantNotFoundError(NotFoundError):
    """Tenant not found error."""

    def __init__(self, tenant_id: Optional[str] = None, **params):
        if tenant_id:
            params["tenant_id"] = tenant_id
        super().__init__(
            error_key="errors.tenant.not_found",
            error_code="TENANT_NOT_FOUND",
            **params
        )


class TenantSuspendedError(ForbiddenError):
    """Tenant suspended error."""

    def __init__(self, tenant_id: Optional[str] = None, **params):
        if tenant_id:
            params["tenant_id"] = tenant_id
        super().__init__(
            error_key="errors.tenant.suspended",
            error_code="TENANT_SUSPENDED",
            **params
        )


class InvalidCredentialsError(UnauthorizedError):
    """Invalid credentials error."""

    def __init__(self, **params):
        super().__init__(
            error_key="errors.auth.invalid_credentials",
            error_code="INVALID_CREDENTIALS",
            **params
        )


class TokenExpiredError(UnauthorizedError):
    """Token expired error."""

    def __init__(self, **params):
        super().__init__(
            error_key="errors.auth.token_expired",
            error_code="TOKEN_EXPIRED",
            **params
        )


class TokenInvalidError(UnauthorizedError):
    """Token invalid error."""

    def __init__(self, **params):
        super().__init__(
            error_key="errors.auth.token_invalid",
            error_code="TOKEN_INVALID",
            **params
        )


class FileNotFoundError(NotFoundError):
    """File not found error."""

    def __init__(self, file_id: Optional[str] = None, **params):
        if file_id:
            params["file_id"] = file_id
        super().__init__(
            error_key="errors.file.not_found",
            error_code="FILE_NOT_FOUND",
            **params
        )


class FileTooLargeError(BadRequestError):
    """File too large error."""

    def __init__(self, max_size: Optional[str] = None, **params):
        if max_size:
            params["max_size"] = max_size
        super().__init__(
            error_key="errors.file.too_large",
            error_code="FILE_TOO_LARGE",
            **params
        )


class InvalidFileTypeError(BadRequestError):
    """Invalid file type error."""

    def __init__(self, file_type: Optional[str] = None, **params):
        if file_type:
            params["file_type"] = file_type
        super().__init__(
            error_key="errors.file.invalid_type",
            error_code="INVALID_FILE_TYPE",
            **params
        )


class RateLimitExceededError(I18nException):
    """Rate limit exceeded error (429)."""

    def __init__(self, **params):
        super().__init__(
            error_key="errors.api.rate_limit_exceeded",
            status_code=429,
            error_code="RATE_LIMIT_EXCEEDED",
            **params
        )


class QuotaExceededError(ForbiddenError):
    """Quota exceeded error."""

    def __init__(self, resource: Optional[str] = None, **params):
        if resource:
            params["resource"] = resource
        super().__init__(
            error_key="errors.api.quota_exceeded",
            error_code="QUOTA_EXCEEDED",
            **params
        )
