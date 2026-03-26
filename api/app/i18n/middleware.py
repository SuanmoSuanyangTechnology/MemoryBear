"""
Language detection middleware for i18n system.

This middleware determines the language to use for each request based on:
1. Query parameter (?lang=en)
2. Accept-Language HTTP header
3. User language preference (from database)
4. Tenant default language
5. System default language

The detected language is injected into request.state.language and
added to the response Content-Language header.
"""

import logging
import re
from typing import Optional

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


class LanguageMiddleware(BaseHTTPMiddleware):
    """
    Language detection middleware.
    
    Determines the language for each request based on multiple sources
    with a clear priority order, validates the language is supported,
    and injects it into the request context.
    """

    async def dispatch(self, request: Request, call_next):
        """
        Process the request and determine the language.
        
        Args:
            request: The incoming request
            call_next: The next middleware/handler in the chain
            
        Returns:
            Response with Content-Language header added
        """
        # Determine the language for this request
        language = await self._determine_language(request)
        
        # Validate language is supported
        from app.core.config import settings
        if language not in settings.I18N_SUPPORTED_LANGUAGES:
            logger.warning(
                f"Unsupported language '{language}' requested, "
                f"falling back to default: {settings.I18N_DEFAULT_LANGUAGE}"
            )
            language = settings.I18N_DEFAULT_LANGUAGE
        
        # Inject language into request state
        request.state.language = language
        
        # Also set in context variable for exception handling
        from app.i18n.exceptions import set_current_locale
        set_current_locale(language)
        
        logger.debug(f"Request language set to: {language}")
        
        # Process the request
        response = await call_next(request)
        
        # Add Content-Language header to response
        response.headers["Content-Language"] = language
        
        return response

    async def _determine_language(self, request: Request) -> str:
        """
        Determine the language to use based on priority order.
        
        Priority:
        1. Query parameter (?lang=en)
        2. Accept-Language HTTP header
        3. User language preference (from database)
        4. Tenant default language
        5. System default language
        
        Args:
            request: The incoming request
            
        Returns:
            Language code (e.g., "zh", "en")
        """
        from app.core.config import settings
        
        # 1. Check query parameter (?lang=en)
        if "lang" in request.query_params:
            lang = request.query_params["lang"].strip().lower()
            if lang:
                logger.debug(f"Language from query parameter: {lang}")
                return lang
        
        # 2. Check Accept-Language HTTP header
        if "Accept-Language" in request.headers:
            lang = self._parse_accept_language(
                request.headers["Accept-Language"]
            )
            if lang:
                logger.debug(f"Language from Accept-Language header: {lang}")
                return lang
        
        # 3. Check user language preference (requires authentication)
        # Note: This assumes user is already loaded into request.state by auth middleware
        if hasattr(request.state, "user") and request.state.user:
            user = request.state.user
            if hasattr(user, "preferred_language") and user.preferred_language:
                logger.debug(
                    f"Language from user preference: {user.preferred_language}"
                )
                return user.preferred_language
        
        # 4. Check tenant default language
        # Note: This assumes tenant is already loaded into request.state
        if hasattr(request.state, "tenant") and request.state.tenant:
            tenant = request.state.tenant
            if hasattr(tenant, "default_language") and tenant.default_language:
                logger.debug(
                    f"Language from tenant default: {tenant.default_language}"
                )
                return tenant.default_language
        
        # 5. Fall back to system default language
        logger.debug(
            f"Using system default language: {settings.I18N_DEFAULT_LANGUAGE}"
        )
        return settings.I18N_DEFAULT_LANGUAGE

    def _parse_accept_language(self, header: str) -> Optional[str]:
        """
        Parse the Accept-Language HTTP header.
        
        The Accept-Language header format:
        Accept-Language: zh-CN,zh;q=0.9,en;q=0.8,en-US;q=0.7
        
        This method:
        1. Parses all language codes and their quality values
        2. Extracts the base language code (zh-CN -> zh)
        3. Sorts by quality value (higher first)
        4. Returns the first supported language
        
        Args:
            header: Accept-Language header value
            
        Returns:
            Language code if found and supported, None otherwise
            
        Examples:
            _parse_accept_language("zh-CN,zh;q=0.9,en;q=0.8")
            # => "zh" (if zh is supported)
            
            _parse_accept_language("en-US,en;q=0.9")
            # => "en" (if en is supported)
        """
        from app.core.config import settings
        
        if not header:
            return None
        
        # Parse language preferences with quality values
        languages = []
        
        for item in header.split(","):
            item = item.strip()
            if not item:
                continue
            
            # Split language code and quality value
            parts = item.split(";")
            lang_code = parts[0].strip()
            
            # Extract base language code (zh-CN -> zh, en-US -> en)
            base_lang = lang_code.split("-")[0].lower()
            
            # Extract quality value (default: 1.0)
            quality = 1.0
            if len(parts) > 1:
                # Look for q=0.9 pattern
                q_match = re.search(r"q=([\d.]+)", parts[1])
                if q_match:
                    try:
                        quality = float(q_match.group(1))
                    except ValueError:
                        quality = 1.0
            
            languages.append((base_lang, quality))
        
        # Sort by quality value (descending)
        languages.sort(key=lambda x: x[1], reverse=True)
        
        # Return the first supported language
        for lang_code, _ in languages:
            if lang_code in settings.I18N_SUPPORTED_LANGUAGES:
                return lang_code
        
        return None
