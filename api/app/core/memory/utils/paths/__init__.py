"""
路径管理模块

包含所有路径管理相关的工具函数。
"""

# 从子模块导出常用函数，保持向后兼容
from .output_paths import (
    get_output_dir,
    get_output_path,
)

__all__ = [
    "get_output_dir",
    "get_output_path",
]
