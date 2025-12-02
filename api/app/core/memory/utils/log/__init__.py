"""
日志管理模块

包含所有日志相关的工具函数。
"""

# 从子模块导出常用函数，保持向后兼容
from .logging_utils import (
    log_prompt_rendering,
    log_template_rendering,
    log_time,
    prompt_logger,
)
from .audit_logger import audit_logger

__all__ = [
    # logging_utils
    "log_prompt_rendering",
    "log_template_rendering",
    "log_time",
    "prompt_logger",
    # audit_logger
    "audit_logger",
]
