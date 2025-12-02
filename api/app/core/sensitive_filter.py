"""
敏感信息过滤器
用于在日志和异常消息中过滤敏感数据
"""
import re
from typing import Any, Dict, List, Set, Union


class SensitiveDataFilter:
    """敏感数据过滤器"""
    
    # 是否启用过滤（从配置读取）
    _enabled: bool = None
    
    @classmethod
    def is_enabled(cls) -> bool:
        """检查过滤器是否启用"""
        if cls._enabled is None:
            from app.core.config import settings
            cls._enabled = settings.ENABLE_SENSITIVE_DATA_FILTER
        return cls._enabled
    
    # 敏感字段关键词（不区分大小写）
    SENSITIVE_KEYS: Set[str] = {
        "password",
        "passwd",
        "pwd",
        "token",
        "access_token",
        "refresh_token",
        "token_id",
        "secret",
        "api_key",
        "apikey",
        "authorization",
        "auth",
        "private_key",
        "secret_key",
        "session_id",
        "sessionid",
        "csrf_token",
        "credit_card",
        "card_number",
        "cvv",
        "ssn",
    }
    
    # 敏感数据的正则模式
    SENSITIVE_PATTERNS: List[tuple] = [
        # Email地址
        (re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'), "[EMAIL]"),
        # 手机号（中国11位）
        (re.compile(r'\b1[3-9]\d{9}\b'), "[PHONE]"),
        # 信用卡号（15-19位数字）
        (re.compile(r'\b\d{15,19}\b'), "[CARD]"),
        # JWT Token (格式: xxx.yyy.zzz) - 必须以eyJ开头，包含至少两个点
        (re.compile(r'\beyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+'), "[TOKEN]"),
        # JWT Token 部分匹配（只有header和payload，没有signature）
        (re.compile(r'\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]*)?'), "[TOKEN]"),
        # UUID格式的token或ID
        (re.compile(r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b', re.IGNORECASE), "[UUID]"),
        # API密钥格式（32位以上的字母数字组合）
        (re.compile(r'\b[A-Za-z0-9]{32,}\b'), "[API_KEY]"),
    ]
    
    # 替换文本
    REDACTED_TEXT = "***REDACTED***"
    
    @classmethod
    def filter_dict(cls, data: Dict[str, Any], deep: bool = True) -> Dict[str, Any]:
        """
        过滤字典中的敏感数据
        
        Args:
            data: 要过滤的字典
            deep: 是否深度过滤嵌套字典
            
        Returns:
            过滤后的字典副本
        """
        if not cls.is_enabled() or not isinstance(data, dict):
            return data
        
        filtered = {}
        for key, value in data.items():
            # 检查键名是否为敏感字段
            if cls._is_sensitive_key(key):
                filtered[key] = cls.REDACTED_TEXT
            elif isinstance(value, dict) and deep:
                # 递归过滤嵌套字典
                filtered[key] = cls.filter_dict(value, deep=True)
            elif isinstance(value, list) and deep:
                # 过滤列表中的字典
                filtered[key] = [
                    cls.filter_dict(item, deep=True) if isinstance(item, dict) else item
                    for item in value
                ]
            elif isinstance(value, str):
                # 过滤字符串中的敏感模式
                filtered[key] = cls.filter_string(value)
            else:
                filtered[key] = value
        
        return filtered
    
    @classmethod
    def filter_string(cls, text: str) -> str:
        """
        过滤字符串中的敏感数据
        
        Args:
            text: 要过滤的字符串
            
        Returns:
            过滤后的字符串
        """
        if not cls.is_enabled() or not isinstance(text, str):
            return text
        
        filtered_text = text
        for pattern, replacement in cls.SENSITIVE_PATTERNS:
            filtered_text = pattern.sub(replacement, filtered_text)
        
        return filtered_text
    
    @classmethod
    def filter_message(cls, message: str, context: Dict[str, Any] = None) -> tuple:
        """
        过滤异常消息和上下文
        
        Args:
            message: 异常消息
            context: 异常上下文字典
            
        Returns:
            (过滤后的消息, 过滤后的上下文)
        """
        filtered_message = cls.filter_string(message)
        filtered_context = cls.filter_dict(context) if context else {}
        
        return filtered_message, filtered_context
    
    @classmethod
    def filter_log_record(cls, record: Dict[str, Any]) -> Dict[str, Any]:
        """
        过滤日志记录
        
        Args:
            record: 日志记录字典
            
        Returns:
            过滤后的日志记录
        """
        filtered = record.copy()
        
        # 过滤消息
        if "message" in filtered:
            filtered["message"] = cls.filter_string(str(filtered["message"]))
        
        # 过滤额外字段
        if "extra" in filtered and isinstance(filtered["extra"], dict):
            filtered["extra"] = cls.filter_dict(filtered["extra"])
        
        # 过滤异常信息
        if "exc_info" in filtered and filtered["exc_info"]:
            # 不过滤堆栈跟踪，但过滤异常消息
            pass
        
        return filtered
    
    @classmethod
    def _is_sensitive_key(cls, key: str) -> bool:
        """
        检查键名是否为敏感字段
        
        Args:
            key: 字段名
            
        Returns:
            是否为敏感字段
        """
        key_lower = key.lower()
        return any(sensitive_key in key_lower for sensitive_key in cls.SENSITIVE_KEYS)
    
    @classmethod
    def sanitize_for_display(cls, value: Any, max_length: int = 100) -> str:
        """
        清理数据用于显示（用于日志或错误消息）
        
        Args:
            value: 要清理的值
            max_length: 最大长度
            
        Returns:
            清理后的字符串
        """
        if value is None:
            return "None"
        
        # 转换为字符串
        str_value = str(value)
        
        # 过滤敏感信息
        filtered_value = cls.filter_string(str_value)
        
        # 截断过长的内容
        if len(filtered_value) > max_length:
            filtered_value = filtered_value[:max_length] + "..."
        
        return filtered_value
