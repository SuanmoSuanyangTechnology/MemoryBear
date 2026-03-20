"""
I18n Management API Schemas

This module defines Pydantic schemas for i18n management APIs.
"""

from pydantic import BaseModel, Field
from typing import Dict, List, Optional, Any


# ============================================================================
# Language Management Schemas
# ============================================================================

class LanguageInfo(BaseModel):
    """Language information"""
    code: str = Field(..., description="Language code (e.g., 'zh', 'en')")
    name: str = Field(..., description="Language name (e.g., 'Chinese', 'English')")
    native_name: str = Field(..., description="Native language name (e.g., '中文', 'English')")
    is_enabled: bool = Field(..., description="Whether the language is enabled")
    is_default: bool = Field(..., description="Whether this is the default language")


class LanguageListResponse(BaseModel):
    """Response for language list"""
    languages: List[LanguageInfo] = Field(..., description="List of available languages")


class LanguageCreateRequest(BaseModel):
    """Request to add a new language"""
    code: str = Field(..., description="Language code (e.g., 'ja', 'ko')", min_length=2, max_length=10)
    name: str = Field(..., description="Language name", min_length=1, max_length=100)
    native_name: str = Field(..., description="Native language name", min_length=1, max_length=100)
    is_enabled: bool = Field(default=True, description="Whether to enable the language")


class LanguageUpdateRequest(BaseModel):
    """Request to update language configuration"""
    is_enabled: Optional[bool] = Field(None, description="Whether the language is enabled")
    is_default: Optional[bool] = Field(None, description="Whether this is the default language")


# ============================================================================
# Translation Management Schemas
# ============================================================================

class TranslationResponse(BaseModel):
    """Response for translation data"""
    translations: Dict[str, Dict[str, Any]] = Field(
        ...,
        description="Translations organized by locale and namespace"
    )


class TranslationUpdateRequest(BaseModel):
    """Request to update a translation"""
    value: str = Field(..., description="New translation value", min_length=1)
    description: Optional[str] = Field(None, description="Optional description of the translation")


class MissingTranslationsResponse(BaseModel):
    """Response for missing translations"""
    missing_translations: Dict[str, List[str]] = Field(
        ...,
        description="Missing translation keys organized by locale"
    )


class ReloadResponse(BaseModel):
    """Response for translation reload"""
    success: bool = Field(..., description="Whether the reload was successful")
    reloaded_locales: List[str] = Field(..., description="List of reloaded locales")
    total_locales: int = Field(..., description="Total number of available locales")
