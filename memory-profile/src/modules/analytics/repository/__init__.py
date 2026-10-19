"""Domain-named analytics repositories over shared infrastructure."""

from src.modules.analytics.repository.end_user_profile import EndUserProfileRepository
from src.modules.analytics.repository.memory_graph import MemoryGraphRepository
from src.modules.analytics.repository.memory_search import MemorySearchRepository
from src.modules.analytics.repository.memory_statistics import MemoryStatisticsRepository
from src.modules.analytics.repository.workspace_statistics import (
    WorkspaceStatisticsCorruptError,
    WorkspaceStatisticsRefreshLockLostError,
    WorkspaceStatisticsRepository,
    WorkspaceStatisticsRepositoryError,
    WorkspaceStatisticsUnavailableError,
)

__all__ = [
    "EndUserProfileRepository",
    "MemoryGraphRepository",
    "MemorySearchRepository",
    "MemoryStatisticsRepository",
    "WorkspaceStatisticsCorruptError",
    "WorkspaceStatisticsRefreshLockLostError",
    "WorkspaceStatisticsRepository",
    "WorkspaceStatisticsRepositoryError",
    "WorkspaceStatisticsUnavailableError",
]
