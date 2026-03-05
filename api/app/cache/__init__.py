"""
Cache 缓存模块

提供各种缓存功能的统一入口
注意：隐性记忆和情绪建议已迁移到数据库存储，不再使用Redis缓存
"""
from .memory import InterestMemoryCache

__all__ = [
    "InterestMemoryCache",
]
