"""Domain-named analytics repositories over shared infrastructure."""

from src.modules.analytics.repository.end_user_profile import EndUserProfileRepository
from src.modules.analytics.repository.memory_graph import MemoryGraphRepository
from src.modules.analytics.repository.memory_statistics import MemoryStatisticsRepository
from src.modules.analytics.repository.workspace_statistics import (
    WorkspaceStatisticsCorruptError,
    WorkspaceStatisticsRepository,
    WorkspaceStatisticsRepositoryError,
    WorkspaceStatisticsUnavailableError,
)

__all__ = [
    "EndUserProfileRepository",
    "MemoryGraphRepository",
    "MemoryStatisticsRepository",
    "WorkspaceStatisticsCorruptError",
    "WorkspaceStatisticsRepository",
    "WorkspaceStatisticsRepositoryError",
    "WorkspaceStatisticsUnavailableError",
]
