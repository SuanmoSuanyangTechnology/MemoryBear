"""
I18n Management API Controller

This module provides management APIs for:
- Language management (list, get, add, update languages)
- Translation management (get, update, reload translations)
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import Callable, Optional

from app.core.logging_config import get_api_logger
from app.core.response_utils import success
from app.db import get_db
from app.dependencies import get_current_user, get_current_superuser
from app.i18n.dependencies import get_translator
from app.i18n.service import get_translation_service
from app.models.user_model import User
from app.schemas.i18n_schema import (
    LanguageInfo,
    LanguageListResponse,
    LanguageCreateRequest,
    LanguageUpdateRequest,
    TranslationResponse,
    TranslationUpdateRequest,
    MissingTranslationsResponse,
    ReloadResponse
)
from app.schemas.response_schema import ApiResponse

api_logger = get_api_logger()

router = APIRouter(
    prefix="/i18n",
    tags=["I18n Management"],
)


# ============================================================================
# Language Management APIs
# ============================================================================

@router.get("/languages", response_model=ApiResponse)
def get_languages(
    t: Callable = Depends(get_translator),
    current_user: User = Depends(get_current_user)
):
    """
    Get list of all supported languages.
    
    Returns:
        List of language information including code, name, and status
    """
    api_logger.info(f"Get languages request from user: {current_user.username}")
    
    from app.core.config import settings
    translation_service = get_translation_service()
    
    # Get available locales from translation service
    available_locales = translation_service.get_available_locales()
    
    # Build language info list
    languages = []
    for locale in available_locales:
        is_default = locale == settings.I18N_DEFAULT_LANGUAGE
        is_enabled = locale in settings.I18N_SUPPORTED_LANGUAGES
        
        # Get native names
        native_names = {
            "zh": "中文（简体）",
            "en": "English",
            "ja": "日本語",
            "ko": "한국어",
            "fr": "Français",
            "de": "Deutsch",
            "es": "Español"
        }
        
        language_info = LanguageInfo(
            code=locale,
            name=f"{locale.upper()}",
            native_name=native_names.get(locale, locale),
            is_enabled=is_enabled,
            is_default=is_default
        )
        languages.append(language_info)
    
    response = LanguageListResponse(languages=languages)
    
    api_logger.info(f"Returning {len(languages)} languages")
    return success(data=response.dict(), msg=t("common.success.retrieved"))


@router.get("/languages/{locale}", response_model=ApiResponse)
def get_language(
    locale: str,
    t: Callable = Depends(get_translator),
    current_user: User = Depends(get_current_user)
):
    """
    Get information about a specific language.
    
    Args:
        locale: Language code (e.g., 'zh', 'en')
        
    Returns:
        Language information
    """
    api_logger.info(f"Get language info request: locale={locale}, user={current_user.username}")
    
    from app.core.config import settings
    translation_service = get_translation_service()
    
    # Check if locale exists
    available_locales = translation_service.get_available_locales()
    if locale not in available_locales:
        api_logger.warning(f"Language not found: {locale}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=t("i18n.language.not_found", locale=locale)
        )
    
    # Build language info
    is_default = locale == settings.I18N_DEFAULT_LANGUAGE
    is_enabled = locale in settings.I18N_SUPPORTED_LANGUAGES
    
    native_names = {
        "zh": "中文（简体）",
        "en": "English",
        "ja": "日本語",
        "ko": "한국어",
        "fr": "Français",
        "de": "Deutsch",
        "es": "Español"
    }
    
    language_info = LanguageInfo(
        code=locale,
        name=f"{locale.upper()}",
        native_name=native_names.get(locale, locale),
        is_enabled=is_enabled,
        is_default=is_default
    )
    
    api_logger.info(f"Returning language info for: {locale}")
    return success(data=language_info.dict(), msg=t("common.success.retrieved"))


@router.post("/languages", response_model=ApiResponse)
def add_language(
    request: LanguageCreateRequest,
    t: Callable = Depends(get_translator),
    current_user: User = Depends(get_current_superuser)
):
    """
    Add a new language (admin only).
    
    Note: This endpoint validates the request but actual language addition
    requires creating translation files in the locales directory.
    
    Args:
        request: Language creation request
        
    Returns:
        Success message
    """
    api_logger.info(
        f"Add language request: code={request.code}, admin={current_user.username}"
    )
    
    from app.core.config import settings
    translation_service = get_translation_service()
    
    # Check if language already exists
    available_locales = translation_service.get_available_locales()
    if request.code in available_locales:
        api_logger.warning(f"Language already exists: {request.code}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=t("i18n.language.already_exists", locale=request.code)
        )
    
    # Note: Actual language addition requires creating translation files
    # This endpoint serves as a validation and documentation point
    
    api_logger.info(
        f"Language addition validated: {request.code}. "
        "Translation files need to be created manually."
    )
    
    return success(
        msg=t(
            "i18n.language.add_instructions",
            locale=request.code,
            dir=settings.I18N_CORE_LOCALES_DIR
        )
    )


@router.put("/languages/{locale}", response_model=ApiResponse)
def update_language(
    locale: str,
    request: LanguageUpdateRequest,
    t: Callable = Depends(get_translator),
    current_user: User = Depends(get_current_superuser)
):
    """
    Update language configuration (admin only).
    
    Note: This endpoint validates the request but actual configuration
    changes require updating environment variables or config files.
    
    Args:
        locale: Language code
        request: Language update request
        
    Returns:
        Success message
    """
    api_logger.info(
        f"Update language request: locale={locale}, admin={current_user.username}"
    )
    
    translation_service = get_translation_service()
    
    # Check if language exists
    available_locales = translation_service.get_available_locales()
    if locale not in available_locales:
        api_logger.warning(f"Language not found: {locale}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=t("i18n.language.not_found", locale=locale)
        )
    
    # Note: Actual configuration changes require updating settings
    # This endpoint serves as a validation and documentation point
    
    api_logger.info(
        f"Language update validated: {locale}. "
        "Configuration changes require environment variable updates."
    )
    
    return success(msg=t("i18n.language.update_instructions", locale=locale))


# ============================================================================
# Translation Management APIs
# ============================================================================

@router.get("/translations", response_model=ApiResponse)
def get_all_translations(
    locale: Optional[str] = None,
    t: Callable = Depends(get_translator),
    current_user: User = Depends(get_current_user)
):
    """
    Get all translations for all or specific locale.
    
    Args:
        locale: Optional locale filter
        
    Returns:
        All translations organized by locale and namespace
    """
    api_logger.info(
        f"Get all translations request: locale={locale}, user={current_user.username}"
    )
    
    translation_service = get_translation_service()
    
    if locale:
        # Get translations for specific locale
        available_locales = translation_service.get_available_locales()
        if locale not in available_locales:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=t("i18n.language.not_found", locale=locale)
            )
        
        translations = {
            locale: translation_service._cache.get(locale, {})
        }
    else:
        # Get all translations
        translations = translation_service._cache
    
    response = TranslationResponse(translations=translations)
    
    api_logger.info(f"Returning translations for: {locale or 'all locales'}")
    return success(data=response.dict(), msg=t("common.success.retrieved"))


@router.get("/translations/{locale}", response_model=ApiResponse)
def get_locale_translations(
    locale: str,
    t: Callable = Depends(get_translator),
    current_user: User = Depends(get_current_user)
):
    """
    Get all translations for a specific locale.
    
    Args:
        locale: Language code
        
    Returns:
        All translations for the locale organized by namespace
    """
    api_logger.info(
        f"Get locale translations request: locale={locale}, user={current_user.username}"
    )
    
    translation_service = get_translation_service()
    
    # Check if locale exists
    available_locales = translation_service.get_available_locales()
    if locale not in available_locales:
        api_logger.warning(f"Language not found: {locale}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=t("i18n.language.not_found", locale=locale)
        )
    
    translations = translation_service._cache.get(locale, {})
    
    api_logger.info(f"Returning {len(translations)} namespaces for locale: {locale}")
    return success(data={"locale": locale, "translations": translations}, msg=t("common.success.retrieved"))


@router.get("/translations/{locale}/{namespace}", response_model=ApiResponse)
def get_namespace_translations(
    locale: str,
    namespace: str,
    t: Callable = Depends(get_translator),
    current_user: User = Depends(get_current_user)
):
    """
    Get translations for a specific namespace in a locale.
    
    Args:
        locale: Language code
        namespace: Translation namespace (e.g., 'common', 'auth')
        
    Returns:
        Translations for the specified namespace
    """
    api_logger.info(
        f"Get namespace translations request: locale={locale}, "
        f"namespace={namespace}, user={current_user.username}"
    )
    
    translation_service = get_translation_service()
    
    # Check if locale exists
    available_locales = translation_service.get_available_locales()
    if locale not in available_locales:
        api_logger.warning(f"Language not found: {locale}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=t("i18n.language.not_found", locale=locale)
        )
    
    # Get namespace translations
    locale_translations = translation_service._cache.get(locale, {})
    namespace_translations = locale_translations.get(namespace, {})
    
    if not namespace_translations:
        api_logger.warning(f"Namespace not found: {namespace} in locale: {locale}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=t("i18n.namespace.not_found", namespace=namespace, locale=locale)
        )
    
    api_logger.info(
        f"Returning translations for namespace: {namespace} in locale: {locale}"
    )
    return success(
        data={
            "locale": locale,
            "namespace": namespace,
            "translations": namespace_translations
        },
        msg=t("common.success.retrieved")
    )


@router.put("/translations/{locale}/{key:path}", response_model=ApiResponse)
def update_translation(
    locale: str,
    key: str,
    request: TranslationUpdateRequest,
    t: Callable = Depends(get_translator),
    current_user: User = Depends(get_current_superuser)
):
    """
    Update a single translation (admin only).
    
    Note: This endpoint validates the request but actual translation updates
    require modifying translation files in the locales directory.
    
    Args:
        locale: Language code
        key: Translation key (format: "namespace.key.subkey")
        request: Translation update request
        
    Returns:
        Success message
    """
    api_logger.info(
        f"Update translation request: locale={locale}, key={key}, "
        f"admin={current_user.username}"
    )
    
    translation_service = get_translation_service()
    
    # Check if locale exists
    available_locales = translation_service.get_available_locales()
    if locale not in available_locales:
        api_logger.warning(f"Language not found: {locale}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=t("i18n.language.not_found", locale=locale)
        )
    
    # Validate key format
    if "." not in key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=t("i18n.translation.invalid_key_format", key=key)
        )
    
    # Note: Actual translation updates require modifying JSON files
    # This endpoint serves as a validation and documentation point
    
    api_logger.info(
        f"Translation update validated: {locale}/{key}. "
        "Translation files need to be updated manually."
    )
    
    return success(
        msg=t("i18n.translation.update_instructions", locale=locale, key=key)
    )


@router.get("/translations/missing", response_model=ApiResponse)
def get_missing_translations(
    locale: Optional[str] = None,
    t: Callable = Depends(get_translator),
    current_user: User = Depends(get_current_user)
):
    """
    Get list of missing translations.
    
    Compares translations across locales to find missing keys.
    
    Args:
        locale: Optional locale to check (defaults to checking all non-default locales)
        
    Returns:
        List of missing translation keys
    """
    api_logger.info(
        f"Get missing translations request: locale={locale}, user={current_user.username}"
    )
    
    from app.core.config import settings
    translation_service = get_translation_service()
    
    default_locale = settings.I18N_DEFAULT_LANGUAGE
    available_locales = translation_service.get_available_locales()
    
    # Get default locale translations as reference
    default_translations = translation_service._cache.get(default_locale, {})
    
    # Collect all keys from default locale
    def collect_keys(data, prefix=""):
        keys = []
        for key, value in data.items():
            full_key = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                keys.extend(collect_keys(value, full_key))
            else:
                keys.append(full_key)
        return keys
    
    default_keys = set()
    for namespace, translations in default_translations.items():
        namespace_keys = collect_keys(translations, namespace)
        default_keys.update(namespace_keys)
    
    # Find missing keys in target locale(s)
    missing_by_locale = {}
    
    target_locales = [locale] if locale else [
        loc for loc in available_locales if loc != default_locale
    ]
    
    for target_locale in target_locales:
        if target_locale not in available_locales:
            continue
        
        target_translations = translation_service._cache.get(target_locale, {})
        target_keys = set()
        
        for namespace, translations in target_translations.items():
            namespace_keys = collect_keys(translations, namespace)
            target_keys.update(namespace_keys)
        
        missing_keys = default_keys - target_keys
        if missing_keys:
            missing_by_locale[target_locale] = sorted(list(missing_keys))
    
    response = MissingTranslationsResponse(missing_translations=missing_by_locale)
    
    total_missing = sum(len(keys) for keys in missing_by_locale.values())
    api_logger.info(f"Found {total_missing} missing translations across {len(missing_by_locale)} locales")
    
    return success(data=response.dict(), msg=t("common.success.retrieved"))


@router.post("/reload", response_model=ApiResponse)
def reload_translations(
    locale: Optional[str] = None,
    t: Callable = Depends(get_translator),
    current_user: User = Depends(get_current_superuser)
):
    """
    Trigger hot reload of translation files (admin only).
    
    Args:
        locale: Optional locale to reload (defaults to reloading all locales)
        
    Returns:
        Reload status and statistics
    """
    api_logger.info(
        f"Reload translations request: locale={locale or 'all'}, "
        f"admin={current_user.username}"
    )
    
    from app.core.config import settings
    
    if not settings.I18N_ENABLE_HOT_RELOAD:
        api_logger.warning("Hot reload is disabled in configuration")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=t("i18n.reload.disabled")
        )
    
    translation_service = get_translation_service()
    
    try:
        # Reload translations
        translation_service.reload(locale)
        
        # Get statistics
        available_locales = translation_service.get_available_locales()
        reloaded_locales = [locale] if locale else available_locales
        
        response = ReloadResponse(
            success=True,
            reloaded_locales=reloaded_locales,
            total_locales=len(available_locales)
        )
        
        api_logger.info(
            f"Successfully reloaded translations for: {', '.join(reloaded_locales)}"
        )
        
        return success(data=response.dict(), msg=t("i18n.reload.success"))
        
    except Exception as e:
        api_logger.error(f"Failed to reload translations: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=t("i18n.reload.failed", error=str(e))
        )


# ============================================================================
# Performance Monitoring APIs
# ============================================================================

@router.get("/metrics", response_model=ApiResponse)
def get_metrics(
    t: Callable = Depends(get_translator),
    current_user: User = Depends(get_current_superuser)
):
    """
    Get i18n performance metrics (admin only).
    
    Returns:
        Performance metrics including:
        - Request counts
        - Missing translations
        - Timing statistics
        - Locale usage
        - Error counts
    """
    api_logger.info(f"Get metrics request: admin={current_user.username}")
    
    translation_service = get_translation_service()
    metrics = translation_service.get_metrics_summary()
    
    api_logger.info("Returning i18n metrics")
    return success(data=metrics, msg=t("common.success.retrieved"))


@router.get("/metrics/cache", response_model=ApiResponse)
def get_cache_stats(
    t: Callable = Depends(get_translator),
    current_user: User = Depends(get_current_superuser)
):
    """
    Get cache statistics (admin only).
    
    Returns:
        Cache statistics including:
        - Hit/miss rates
        - LRU cache performance
        - Loaded locales
        - Memory usage
    """
    api_logger.info(f"Get cache stats request: admin={current_user.username}")
    
    translation_service = get_translation_service()
    cache_stats = translation_service.get_cache_stats()
    memory_usage = translation_service.get_memory_usage()
    
    data = {
        "cache": cache_stats,
        "memory": memory_usage
    }
    
    api_logger.info("Returning cache statistics")
    return success(data=data, msg=t("common.success.retrieved"))


@router.get("/metrics/prometheus")
def get_prometheus_metrics(
    current_user: User = Depends(get_current_superuser)
):
    """
    Get metrics in Prometheus format (admin only).
    
    Returns:
        Prometheus-formatted metrics as plain text
    """
    api_logger.info(f"Get Prometheus metrics request: admin={current_user.username}")
    
    from app.i18n.metrics import get_metrics
    metrics = get_metrics()
    prometheus_output = metrics.export_prometheus()
    
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(content=prometheus_output)


@router.post("/metrics/reset", response_model=ApiResponse)
def reset_metrics(
    t: Callable = Depends(get_translator),
    current_user: User = Depends(get_current_superuser)
):
    """
    Reset all metrics (admin only).
    
    Returns:
        Success message
    """
    api_logger.info(f"Reset metrics request: admin={current_user.username}")
    
    from app.i18n.metrics import get_metrics
    metrics = get_metrics()
    metrics.reset()
    
    translation_service = get_translation_service()
    translation_service.cache.reset_stats()
    
    api_logger.info("Metrics reset completed")
    return success(msg=t("i18n.metrics.reset_success"))


# ============================================================================
# Missing Translation Logging and Reporting APIs
# ============================================================================

@router.get("/logs/missing", response_model=ApiResponse)
def get_missing_translation_logs(
    locale: Optional[str] = None,
    limit: Optional[int] = 100,
    t: Callable = Depends(get_translator),
    current_user: User = Depends(get_current_superuser)
):
    """
    Get missing translation logs (admin only).
    
    Returns logged missing translations with context information.
    
    Args:
        locale: Optional locale filter
        limit: Maximum number of entries to return (default: 100)
        
    Returns:
        Missing translation logs with context
    """
    api_logger.info(
        f"Get missing translation logs request: locale={locale}, "
        f"limit={limit}, admin={current_user.username}"
    )
    
    translation_service = get_translation_service()
    translation_logger = translation_service.translation_logger
    
    # Get missing translations
    missing_translations = translation_logger.get_missing_translations(locale)
    
    # Get missing with context
    missing_with_context = translation_logger.get_missing_with_context(locale, limit)
    
    # Get statistics
    statistics = translation_logger.get_statistics()
    
    data = {
        "missing_translations": missing_translations,
        "recent_context": missing_with_context,
        "statistics": statistics
    }
    
    api_logger.info(
        f"Returning {statistics['total_missing']} missing translations"
    )
    return success(data=data, msg=t("common.success.retrieved"))


@router.get("/logs/missing/report", response_model=ApiResponse)
def generate_missing_translation_report(
    locale: Optional[str] = None,
    t: Callable = Depends(get_translator),
    current_user: User = Depends(get_current_superuser)
):
    """
    Generate a comprehensive missing translation report (admin only).
    
    Args:
        locale: Optional locale filter
        
    Returns:
        Comprehensive report with missing translations and statistics
    """
    api_logger.info(
        f"Generate missing translation report request: locale={locale}, "
        f"admin={current_user.username}"
    )
    
    translation_service = get_translation_service()
    translation_logger = translation_service.translation_logger
    
    # Generate report
    report = translation_logger.generate_report(locale)
    
    api_logger.info(
        f"Generated report with {report['total_missing']} missing translations"
    )
    return success(data=report, msg=t("common.success.retrieved"))


@router.post("/logs/missing/export", response_model=ApiResponse)
def export_missing_translations(
    locale: Optional[str] = None,
    t: Callable = Depends(get_translator),
    current_user: User = Depends(get_current_superuser)
):
    """
    Export missing translations to JSON file (admin only).
    
    Args:
        locale: Optional locale filter
        
    Returns:
        Export status and file path
    """
    api_logger.info(
        f"Export missing translations request: locale={locale}, "
        f"admin={current_user.username}"
    )
    
    from datetime import datetime
    translation_service = get_translation_service()
    translation_logger = translation_service.translation_logger
    
    # Generate filename with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    locale_suffix = f"_{locale}" if locale else "_all"
    output_file = f"logs/i18n/missing_translations{locale_suffix}_{timestamp}.json"
    
    # Export to file
    translation_logger.export_to_json(output_file)
    
    api_logger.info(f"Missing translations exported to: {output_file}")
    return success(
        data={"file_path": output_file},
        msg=t("i18n.logs.export_success", file=output_file)
    )


@router.delete("/logs/missing", response_model=ApiResponse)
def clear_missing_translation_logs(
    locale: Optional[str] = None,
    t: Callable = Depends(get_translator),
    current_user: User = Depends(get_current_superuser)
):
    """
    Clear missing translation logs (admin only).
    
    Args:
        locale: Optional locale to clear (clears all if not specified)
        
    Returns:
        Success message
    """
    api_logger.info(
        f"Clear missing translation logs request: locale={locale or 'all'}, "
        f"admin={current_user.username}"
    )
    
    translation_service = get_translation_service()
    translation_logger = translation_service.translation_logger
    
    # Clear logs
    translation_logger.clear(locale)
    
    api_logger.info(f"Cleared missing translation logs for: {locale or 'all locales'}")
    return success(msg=t("i18n.logs.clear_success"))
