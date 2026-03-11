"""
FastAPI dependency injection functions for i18n.

This module provides dependency injection functions that can be used
in FastAPI route handlers to access the current language and translator.
"""

import logging
from typing import Callable

from fastapi import Request

from app.i18n.service import get_translation_service

logger = logging.getLogger(__name__)


async def get_current_language(request: Request) -> str:
    """
    Get the current language from the request context.

    This dependency extracts the language that was determined by the
    LanguageMiddleware and stored in request.state.

    Args:
        request: FastAPI request object

    Returns:
        Language code (e.g., "zh", "en")

    Usage:
        @router.get("/example")
        async def example(language: str = Depends(get_current_language)):
            return {"language": language}
    """
    # Get language from request state (set by LanguageMiddleware)
    language = getattr(request.state, "language", None)

    if language is None:
        # Fallback to default language if not set
        from app.core.config import settings
        language = settings.I18N_DEFAULT_LANGUAGE
        logger.warning(
            "Language not found in request.state, using default: "
            f"{language}"
        )

    return language


async def get_translator(request: Request) -> Callable:
    """
    Get a translator function bound to the current request's language.

    This dependency returns a translation function that automatically
    uses the current request's language, making it easy to translate
    strings in route handlers.

    Args:
        request: FastAPI request object

    Returns:
        Translation function with signature: t(key: str, **params) -> str

    Usage:
        @router.post("/workspaces")
        async def create_workspace(
            data: WorkspaceCreate,
            t: Callable = Depends(get_translator)
        ):
            workspace = await workspace_service.create(data)
            return {
                "success": True,
                "message": t("workspace.created_successfully"),
                "data": workspace
            }

        # With parameters
        @router.get("/items")
        async def get_items(t: Callable = Depends(get_translator)):
            count = 5
            return {
                "message": t("items.found", count=count)
            }
    """
    # Get current language
    language = await get_current_language(request)

    # Get translation service
    service = get_translation_service()

    # Return a bound translation function
    def translate(key: str, **params) -> str:
        """
        Translate a key using the current request's language.

        Args:
            key: Translation key (e.g., "common.success.created")
            **params: Parameters for parameterized messages

        Returns:
            Translated string
        """
        return service.translate(key, language, **params)

    return translate


async def get_enum_translator(request: Request) -> Callable:
    """
    Get an enum translator function bound to the current request's language.

    This dependency returns a function for translating enum values
    that automatically uses the current request's language.

    Args:
        request: FastAPI request object

    Returns:
        Enum translation function with signature:
        t_enum(enum_type: str, value: str) -> str

    Usage:
        @router.get("/workspace/{id}")
        async def get_workspace(
            id: str,
            t_enum: Callable = Depends(get_enum_translator)
        ):
            workspace = await workspace_service.get(id)
            return {
                "id": workspace.id,
                "role": workspace.role,
                "role_display": t_enum("workspace_role", workspace.role),
                "status": workspace.status,
                "status_display": t_enum("workspace_status", workspace.status)
            }
    """
    # Get current language
    language = await get_current_language(request)

    # Get translation service
    service = get_translation_service()

    # Return a bound enum translation function
    def translate_enum(enum_type: str, value: str) -> str:
        """
        Translate an enum value using the current request's language.

        Args:
            enum_type: Enum type name (e.g., "workspace_role")
            value: Enum value (e.g., "manager")

        Returns:
            Translated enum display name
        """
        return service.translate_enum(enum_type, value, language)

    return translate_enum
