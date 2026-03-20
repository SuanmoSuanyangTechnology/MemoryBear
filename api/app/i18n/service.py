"""
Translation service for i18n system.

This module provides the core translation functionality including:
- Translation lookup with fallback mechanism
- Parameterized message support
- Enum value translation
- Memory caching for performance
- Performance monitoring and metrics
"""

import logging
from functools import lru_cache
from typing import Any, Dict, Optional

from app.i18n.loader import TranslationLoader
from app.i18n.cache import TranslationCache
from app.i18n.metrics import get_metrics, monitor_performance, track_missing_translation, track_translation_request
from app.i18n.logger import get_translation_logger

logger = logging.getLogger(__name__)


class TranslationService:
    """
    Translation service that provides:
    - Fast translation lookup with memory cache
    - Parameterized message support ({param} syntax)
    - Fallback mechanism (current locale → default locale → key)
    - Enum value translation
    - Deep merge of multi-directory translations
    """

    def __init__(self, locales_dirs: Optional[list] = None):
        """
        Initialize the translation service.

        Args:
            locales_dirs: List of directories containing translation files.
                         If None, will auto-detect from settings.
        """
        from app.core.config import settings

        self.loader = TranslationLoader(locales_dirs)
        self.default_locale = settings.I18N_DEFAULT_LANGUAGE
        self.fallback_locale = settings.I18N_FALLBACK_LANGUAGE
        self.log_missing = settings.I18N_LOG_MISSING_TRANSLATIONS
        self.enable_cache = settings.I18N_ENABLE_TRANSLATION_CACHE

        # Initialize advanced cache with LRU
        lru_cache_size = getattr(settings, 'I18N_LRU_CACHE_SIZE', 1000)
        self.cache = TranslationCache(
            max_lru_size=lru_cache_size,
            enable_lazy_load=False  # Load all at startup for now
        )

        # Load all translations into cache
        self._load_all_locales()

        # Initialize metrics
        self.metrics = get_metrics()
        
        # Initialize translation logger
        self.translation_logger = get_translation_logger()

        logger.info(
            f"TranslationService initialized with default locale: {self.default_locale}, "
            f"LRU cache size: {lru_cache_size}"
        )

    def _load_all_locales(self):
        """Load all available locales into memory cache."""
        available_locales = self.loader.get_available_locales()
        logger.info(f"Loading translations for locales: {available_locales}")

        for locale in available_locales:
            locale_data = self.loader.load_locale(locale)
            self.cache.set_locale_data(locale, locale_data)

        logger.info(f"Loaded {len(available_locales)} locales into cache")

    @monitor_performance("translate")
    def translate(
        self,
        key: str,
        locale: Optional[str] = None,
        **params
    ) -> str:
        """
        Translate a key to the target locale.

        Supports:
        - Dot-separated keys (e.g., "common.success.created")
        - Parameterized messages (e.g., "Hello {name}")
        - Fallback mechanism

        Args:
            key: Translation key (format: "namespace.key.subkey")
            locale: Target locale (defaults to default locale)
            **params: Parameters for parameterized messages

        Returns:
            Translated string, or the key itself if translation not found

        Examples:
            translate("common.success.created", "zh")
            # => "创建成功"

            translate("common.validation.required", "zh", field="名称")
            # => "名称不能为空"
        """
        if locale is None:
            locale = self.default_locale

        # Parse key (namespace.key.subkey)
        parts = key.split(".", 1)
        if len(parts) < 2:
            if self.log_missing:
                logger.warning(f"Invalid translation key format: {key}")
            return key

        namespace = parts[0]
        key_path = parts[1].split(".")

        # Track request
        track_translation_request(locale, namespace)

        # Get translation from cache
        translation = self.cache.get_translation(locale, namespace, key_path)

        # Fallback to default locale if not found
        if translation is None and locale != self.fallback_locale:
            translation = self.cache.get_translation(
                self.fallback_locale, namespace, key_path
            )

        # If still not found, return the key itself
        if translation is None:
            if self.log_missing:
                logger.warning(
                    f"Missing translation: {key} (locale: {locale})"
                )
                track_missing_translation(key, locale)
                
                # Log to translation logger with context
                self.translation_logger.log_missing_translation(
                    key=key,
                    locale=locale,
                    context={"namespace": namespace}
                )
            return key

        # Apply parameters if provided
        if params:
            try:
                translation = translation.format(**params)
            except KeyError as e:
                error_msg = f"Missing parameter in translation '{key}': {e}"
                logger.error(error_msg)
                self.translation_logger.log_translation_error(
                    error_type="parameter_missing",
                    message=error_msg,
                    key=key,
                    locale=locale,
                    context={"params": list(params.keys())}
                )
            except Exception as e:
                error_msg = f"Error formatting translation '{key}': {e}"
                logger.error(error_msg)
                self.translation_logger.log_translation_error(
                    error_type="format_error",
                    message=error_msg,
                    key=key,
                    locale=locale
                )

        return translation

    def _get_translation(
        self,
        locale: str,
        namespace: str,
        key_path: list
    ) -> Optional[str]:
        """
        Get translation from cache (deprecated, use cache.get_translation).

        Args:
            locale: Locale code
            namespace: Translation namespace
            key_path: List of nested keys

        Returns:
            Translation string or None if not found
        """
        return self.cache.get_translation(locale, namespace, key_path)

    @monitor_performance("translate_enum")
    def translate_enum(
        self,
        enum_type: str,
        value: str,
        locale: Optional[str] = None
    ) -> str:
        """
        Translate an enum value.

        Args:
            enum_type: Enum type name (e.g., "workspace_role")
            value: Enum value (e.g., "manager")
            locale: Target locale

        Returns:
            Translated enum display name

        Examples:
            translate_enum("workspace_role", "manager", "zh")
            # => "管理员"

            translate_enum("invite_status", "pending", "en")
            # => "Pending"
        """
        key = f"enums.{enum_type}.{value}"
        return self.translate(key, locale)

    def has_translation(self, key: str, locale: str) -> bool:
        """
        Check if a translation exists for the given key and locale.

        Args:
            key: Translation key
            locale: Locale code

        Returns:
            True if translation exists, False otherwise
        """
        parts = key.split(".", 1)
        if len(parts) < 2:
            return False

        namespace = parts[0]
        key_path = parts[1].split(".")

        translation = self.cache.get_translation(locale, namespace, key_path)
        return translation is not None

    def reload(self, locale: Optional[str] = None):
        """
        Reload translation files.

        Args:
            locale: Specific locale to reload. If None, reloads all locales.
        """
        logger.info(f"Reloading translations for locale: {locale or 'all'}")

        if locale:
            locale_data = self.loader.load_locale(locale)
            self.cache.set_locale_data(locale, locale_data)
            # Clear LRU cache for this locale
            self.cache.clear_locale(locale)
        else:
            self._load_all_locales()
            # Clear all LRU cache
            self.cache.clear_lru()

        logger.info("Translation reload completed")

    def get_available_locales(self) -> list:
        """
        Get list of all available locales.

        Returns:
            List of locale codes
        """
        return self.cache.get_loaded_locales()
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics.
        
        Returns:
            Dictionary with cache statistics
        """
        return self.cache.get_stats()
    
    def get_metrics_summary(self) -> Dict[str, Any]:
        """
        Get metrics summary.
        
        Returns:
            Dictionary with metrics summary
        """
        return self.metrics.get_summary()
    
    def get_memory_usage(self) -> Dict[str, Any]:
        """
        Get memory usage information.
        
        Returns:
            Dictionary with memory usage information
        """
        return self.cache.get_memory_usage()

    def get_loaded_dirs(self) -> list:
        """
        Get list of loaded translation directories.

        Returns:
            List of directory paths
        """
        return self.loader.locales_dirs


# Global singleton instance
_translation_service: Optional[TranslationService] = None


def get_translation_service() -> TranslationService:
    """
    Get the global translation service instance.

    Returns:
        TranslationService singleton
    """
    global _translation_service
    if _translation_service is None:
        _translation_service = TranslationService()
    return _translation_service


# Convenience functions for easy access
def t(key: str, locale: Optional[str] = None, **params) -> str:
    """
    Translate a key (convenience function).

    Args:
        key: Translation key
        locale: Target locale (optional, uses default if not provided)
        **params: Parameters for parameterized messages

    Returns:
        Translated string

    Examples:
        t("common.success.created")
        t("common.validation.required", field="名称")
        t("workspace.member_count", count=5)
    """
    service = get_translation_service()
    return service.translate(key, locale, **params)


def t_enum(enum_type: str, value: str, locale: Optional[str] = None) -> str:
    """
    Translate an enum value (convenience function).

    Args:
        enum_type: Enum type name
        value: Enum value
        locale: Target locale

    Returns:
        Translated enum display name

    Examples:
        t_enum("workspace_role", "manager")
        t_enum("invite_status", "pending", "en")
    """
    service = get_translation_service()
    return service.translate_enum(enum_type, value, locale)
