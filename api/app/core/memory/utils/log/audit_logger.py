"""
配置审计日志记录器

提供专门的审计日志功能，用于追踪配置变更和操作记录。
"""
import logging
import os
from datetime import datetime
from typing import Optional, Dict, Any


def _format_value(value: Any) -> str:
    """
    格式化值为字符串，特殊处理 UUID 等对象
    
    Args:
        value: 要格式化的值
        
    Returns:
        str: 格式化后的字符串
    """
    if value is None:
        return "None"
    elif isinstance(value, bool):
        return str(value)
    elif hasattr(value, 'hex'):  # UUID 对象有 hex 属性
        return str(value)  # 使用标准的 UUID 字符串格式（带连字符）
    else:
        return str(value)


class ConfigAuditLogger:
    """配置审计日志记录器"""
    
    def __init__(self, log_file: str = "logs/config_audit.log"):
        """
        初始化审计日志记录器
        
        Args:
            log_file: 日志文件路径
        """
        self.logger = logging.getLogger("config_audit")
        self.logger.setLevel(logging.INFO)
        
        # 避免重复添加处理器
        if not self.logger.handlers:
            # 确保日志目录存在
            log_dir = os.path.dirname(log_file)
            if log_dir and not os.path.exists(log_dir):
                os.makedirs(log_dir, exist_ok=True)
            
            # 创建文件处理器
            handler = logging.FileHandler(log_file, encoding='utf-8')
            formatter = logging.Formatter(
                '%(asctime)s [AUDIT] %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
    
    def log_config_load(
        self,
        config_id: str,
        user_id: Optional[str] = None,
        group_id: Optional[str] = None,
        success: bool = True,
        details: Optional[Dict[str, Any]] = None
    ):
        """
        记录配置加载事件
        
        Args:
            config_id: 配置 ID
            user_id: 用户 ID（可选）
            group_id: 组 ID（可选）
            success: 是否成功
            details: 详细信息（可选）
        """
        result = "SUCCESS" if success else "FAILED"
        msg = (
            f"CONFIG_LOAD config_id={config_id} "
            f"user={user_id or 'N/A'} group={group_id or 'N/A'} "
            f"result={result}"
        )
        if details:
            # 格式化详细信息，确保所有值都正确转换为字符串
            details_str = ", ".join(f"{k}={_format_value(v)}" for k, v in details.items())
            msg += f" details=[{details_str}]"
        self.logger.info(msg)
    
    def log_config_change(
        self,
        config_id: str,
        old_values: Dict[str, Any],
        new_values: Dict[str, Any],
        user_id: Optional[str] = None
    ):
        """
        记录配置变更事件
        
        Args:
            config_id: 配置 ID
            old_values: 旧配置值
            new_values: 新配置值
            user_id: 用户 ID（可选）
        """
        changes = []
        for key in new_values:
            if key in old_values and old_values[key] != new_values[key]:
                changes.append(f"{key}: {old_values[key]} -> {new_values[key]}")
        
        if changes:
            msg = (
                f"CONFIG_CHANGE config_id={config_id} "
                f"user={user_id or 'N/A'} "
                f"changes=[{', '.join(changes)}]"
            )
            self.logger.info(msg)
    
    def log_operation(
        self,
        operation: str,
        config_id: str,
        group_id: str,
        success: bool = True,
        duration: Optional[float] = None,
        error: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None
    ):
        """
        记录操作事件
        
        Args:
            operation: 操作类型（WRITE, READ 等）
            config_id: 配置 ID
            group_id: 组 ID
            success: 是否成功
            duration: 操作耗时（秒）
            error: 错误信息（可选）
            details: 详细信息（可选）
        """
        result = "SUCCESS" if success else "FAILED"
        msg = (
            f"{operation.upper()} config_id={config_id} "
            f"group={group_id} result={result}"
        )
        if duration is not None:
            msg += f" duration={duration:.2f}s"
        if error:
            msg += f" error={error}"
        if details:
            # 格式化详细信息，确保所有值都正确转换为字符串
            details_str = ", ".join(f"{k}={_format_value(v)}" for k, v in details.items())
            msg += f" details=[{details_str}]"
        self.logger.info(msg)
    
    def log_cache_event(
        self,
        event_type: str,
        config_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None
    ):
        """
        记录缓存事件
        
        Args:
            event_type: 事件类型（HIT, MISS, CLEAR, EXPIRE）
            config_id: 配置 ID（可选）
            details: 详细信息（可选）
        """
        msg = f"CACHE_{event_type.upper()}"
        if config_id:
            msg += f" config_id={config_id}"
        if details:
            # 格式化详细信息，确保所有值都正确转换为字符串
            details_str = ", ".join(f"{k}={_format_value(v)}" for k, v in details.items())
            msg += f" details=[{details_str}]"
        self.logger.info(msg)


# 全局审计日志记录器实例
audit_logger = ConfigAuditLogger()
