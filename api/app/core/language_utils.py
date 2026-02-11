# -*- coding: utf-8 -*-
"""语言处理工具模块

本模块提供集中化的语言校验和处理功能，确保整个应用中语言参数的一致性。

Functions:
    validate_language: 校验语言参数，确保其为有效值
    get_language_from_header: 从请求头获取并校验语言参数
"""

from typing import Optional

from app.core.logging_config import get_logger

logger = get_logger(__name__)

# 支持的语言列表
SUPPORTED_LANGUAGES = {"zh", "en"}

# 默认回退语言
DEFAULT_LANGUAGE = "zh"


def validate_language(language: Optional[str]) -> str:
    """
    校验语言参数，确保其为有效值。
    
    Args:
        language: 待校验的语言代码，可以是 None、"zh"、"en" 或其他值
        
    Returns:
        有效的语言代码（"zh" 或 "en"）
        
    Examples:
        >>> validate_language("zh")
        'zh'
        >>> validate_language("en")
        'en'
        >>> validate_language("EN")  # 大小写不敏感
        'en'
        >>> validate_language(None)  # None 回退到默认值
        'zh'
        >>> validate_language("fr")  # 不支持的语言回退到默认值
        'zh'
    """
    if language is None:
        return DEFAULT_LANGUAGE
    
    # 标准化：转小写并去除空白
    lang = str(language).lower().strip()
    
    if lang in SUPPORTED_LANGUAGES:
        return lang
    
    logger.warning(
        f"无效的语言参数 '{language}'，已回退到默认值 '{DEFAULT_LANGUAGE}'。"
        f"支持的语言: {SUPPORTED_LANGUAGES}"
    )
    return DEFAULT_LANGUAGE


def get_language_from_header(language_type: Optional[str]) -> str:
    """
    从请求头获取并校验语言参数。
    
    这是一个便捷函数，用于在 controller 层统一处理 X-Language-Type Header。
    
    Args:
        language_type: 从 X-Language-Type Header 获取的语言值
        
    Returns:
        有效的语言代码（"zh" 或 "en"）
        
    Examples:
        >>> get_language_from_header(None)  # Header 未传递
        'zh'
        >>> get_language_from_header("en")
        'en'
        >>> get_language_from_header("invalid")  # 无效值回退
        'zh'
    """
    return validate_language(language_type)
