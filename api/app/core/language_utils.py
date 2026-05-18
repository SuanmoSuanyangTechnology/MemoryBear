# -*- coding: utf-8 -*-
"""语言处理工具模块

本模块提供集中化的语言校验和处理功能，确保整个应用中语言参数的一致性。

Functions:
    validate_language: 校验语言参数，确保其为有效值
    get_language_from_header: 从请求头获取并校验语言参数
    detect_text_language: 根据文本内容检测语言
"""

import re
from typing import Optional

from app.core.logging_config import get_logger

logger = get_logger(__name__)

# 支持的语言列表
SUPPORTED_LANGUAGES = {"zh", "en"}

# 默认回退语言
DEFAULT_LANGUAGE = "zh"

# CJK Unicode 范围（中文字符）
_CJK_RANGES = (
    r"[\u4e00-\u9fff"       # CJK Unified Ideographs
    r"\u3400-\u4dbf"        # CJK Unified Ideographs Extension A
    r"\uf900-\ufaff"        # CJK Compatibility Ideographs
    r"\u2e80-\u2eff"        # CJK Radicals Supplement
    r"\u3000-\u303f"        # CJK Symbols and Punctuation
    r"\uff00-\uffef]"       # Halfwidth and Fullwidth Forms
)
_CJK_PATTERN = re.compile(_CJK_RANGES)


def detect_text_language(text: Optional[str], fallback: str = "zh") -> str:
    """根据文本内容检测主要语言。

    使用简单的中文字符占比启发式方法：
    - 统计文本中 CJK 字符数量与总有效字符（去除空白和标点后）的比例
    - 中文字符占比 >= 10% 则判定为中文，否则判定为英文

    此方法适用于记忆写入流水线中的输入文本语言检测，确保 LLM 输出语言
    跟随输入内容语言，而非 X-Language-Type header。

    Args:
        text: 待检测的文本内容
        fallback: 无法判断时的回退语言（默认 "zh"）

    Returns:
        "zh" 或 "en"

    Examples:
        >>> detect_text_language("我今年32岁，在上海工作。")
        'zh'
        >>> detect_text_language("I am a backend engineer.")
        'en'
        >>> detect_text_language("我使用 Python 和 JavaScript 开发。")
        'zh'
        >>> detect_text_language("")
        'zh'
    """
    if not text or not text.strip():
        return fallback

    # 统计 CJK 字符数
    cjk_chars = _CJK_PATTERN.findall(text)
    cjk_count = len(cjk_chars)

    # 统计总有效字符数（去除空白）
    non_space = re.sub(r"\s", "", text)
    total_chars = len(non_space)

    if total_chars == 0:
        return fallback

    # 中文字符占比 >= 10% 则判定为中文
    ratio = cjk_count / total_chars
    if ratio >= 0.1:
        return "zh"
    else:
        return "en"


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
    
    # 处理枚举类型：优先取 .value，避免 str(Language.ZH) → "Language.ZH"
    if hasattr(language, "value"):
        language = language.value
    
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
