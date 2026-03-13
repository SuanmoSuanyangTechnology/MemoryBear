"""
Internationalization (i18n) module for MemoryBear Enterprise.

This module provides complete i18n support for the backend API including:
- Translation loading from multiple directories (community + enterprise)
- Translation service with caching and fallback
- Language detection middleware
- Dependency injection for FastAPI
- Convenience functions for easy usage

Usage:
    from app.i18n import t, t_enum

    # Simple translation
    message = t("common.success.created")

    # Parameterized translation
    error = t("common.validation.required", field="名称")

    # Enum translation
    role_display = t_enum("workspace_role", "manager")
"""

from app.i18n.dependencies import (
    get_current_language,
    get_enum_translator,
    get_translator,
)
from app.i18n.exceptions import (
    BadRequestError,
    ConflictError,
    FileNotFoundError,
    FileTooLargeError,
    ForbiddenError,
    I18nException,
    InternalServerError,
    InvalidCredentialsError,
    InvalidFileTypeError,
    NotFoundError,
    QuotaExceededError,
    RateLimitExceededError,
    ServiceUnavailableError,
    TenantNotFoundError,
    TenantSuspendedError,
    TokenExpiredError,
    TokenInvalidError,
    UnauthorizedError,
    UserAlreadyExistsError,
    UserNotFoundError,
    ValidationError,
    WorkspaceNotFoundError,
    WorkspacePermissionDeniedError,
    get_current_locale,
    set_current_locale,
)
from app.i18n.loader import TranslationLoader
from app.i18n.logger import (
    TranslationLogger,
    get_translation_logger,
    log_missing_translation,
    log_translation_error,
)
from app.i18n.middleware import LanguageMiddleware
from app.i18n.serializers import (
    I18nResponseMixin,
    WorkspaceSerializer,
    WorkspaceMemberSerializer,
    WorkspaceInviteSerializer,
)
from app.i18n.service import (
    TranslationService,
    get_translation_service,
    t,
    t_enum,
)

__all__ = [
    "TranslationLoader",
    "LanguageMiddleware",
    "TranslationService",
    "get_translation_service",
    "t",
    "t_enum",
    "get_current_language",
    "get_translator",
    "get_enum_translator",
    # Context management
    "get_current_locale",
    "set_current_locale",
    # Logging
    "TranslationLogger",
    "get_translation_logger",
    "log_missing_translation",
    "log_translation_error",
    # Serializers
    "I18nResponseMixin",
    "WorkspaceSerializer",
    "WorkspaceMemberSerializer",
    "WorkspaceInviteSerializer",
    # Exception classes
    "I18nException",
    "BadRequestError",
    "UnauthorizedError",
    "ForbiddenError",
    "NotFoundError",
    "ConflictError",
    "ValidationError",
    "InternalServerError",
    "ServiceUnavailableError",
    "WorkspaceNotFoundError",
    "WorkspacePermissionDeniedError",
    "UserNotFoundError",
    "UserAlreadyExistsError",
    "TenantNotFoundError",
    "TenantSuspendedError",
    "InvalidCredentialsError",
    "TokenExpiredError",
    "TokenInvalidError",
    "FileNotFoundError",
    "FileTooLargeError",
    "InvalidFileTypeError",
    "RateLimitExceededError",
    "QuotaExceededError",
]
