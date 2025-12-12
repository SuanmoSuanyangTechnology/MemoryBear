"""
Memory Analytics Module

This module provides analytics and insights for the memory system.

Available functions:
- get_hot_memory_tags: Get hot memory tags by frequency
- MemoryInsight: Generate memory insight reports
- get_recent_activity_stats: Get recent activity statistics
- generate_user_summary: Generate user summary
"""

from app.core.memory.analytics.hot_memory_tags import get_hot_memory_tags
from app.core.memory.analytics.memory_insight import MemoryInsight
from app.core.memory.analytics.recent_activity_stats import get_recent_activity_stats
from app.core.memory.analytics.user_summary import generate_user_summary

__all__ = [
    "get_hot_memory_tags",
    "MemoryInsight",
    "get_recent_activity_stats",
    "generate_user_summary",
]
