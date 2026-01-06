"""
Memory Analytics Module

This module provides analytics and insights for the memory system.

Available functions:
- get_hot_memory_tags: Get hot memory tags by frequency
- get_recent_activity_stats: Get recent activity statistics

Note: MemoryInsight and generate_user_summary have been moved to 
app.services.user_memory_service for better architecture.
"""

from app.core.memory.analytics.hot_memory_tags import get_hot_memory_tags
from app.core.memory.analytics.recent_activity_stats import get_recent_activity_stats

__all__ = [
    "get_hot_memory_tags",
    "get_recent_activity_stats",
]
