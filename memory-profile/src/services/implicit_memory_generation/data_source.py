"""
Memory Data Source

Handles retrieval and processing of memory data from Neo4j using direct Cypher queries.
"""

import logging
from datetime import datetime
from typing import Any

from src.common.timing import utcnow_naive
from src.infrastructure.neo4j import Neo4jManager
from src.repositories.implicit_memory_generation import (
    ImplicitMemorySummaryRepository,
)
from src.schemas.implicit_memory_generation import TimeRange, UserMemorySummary

logger = logging.getLogger(__name__)


class MemoryDataSource:
    """Retrieves processed memory data from Neo4j using direct Cypher queries."""

    def __init__(self, neo4j: Neo4jManager):
        self.implicit_memory_summary_repo = ImplicitMemorySummaryRepository(neo4j)

    def _parse_timestamp(self, timestamp: Any) -> datetime:
        """Parse timestamp from various formats."""
        if isinstance(timestamp, str):
            return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        elif timestamp is None:
            return utcnow_naive()
        # neo4j.time.DateTime → 标准 datetime.datetime
        if hasattr(timestamp, "to_native"):
            return timestamp.to_native()
        return timestamp

    def _dict_to_user_summary(self, summary_dict: dict, user_id: str) -> UserMemorySummary | None:
        """Convert a Neo4j dict directly to UserMemorySummary."""
        try:
            content = summary_dict.get("content", summary_dict.get("summary", ""))
            if not content or not content.strip():
                return None

            return UserMemorySummary(
                summary_id=summary_dict.get("id", summary_dict.get("uuid", "")),
                user_id=user_id,
                user_content=content,
                timestamp=self._parse_timestamp(summary_dict.get("created_at")),
                confidence_score=1.0,
                summary_type=summary_dict.get("summary_type", "scene_summary"),
            )
        except Exception as e:
            logger.warning(f"Failed to parse summary {summary_dict.get('id', 'unknown')}: {e}")
            return None

    async def count_user_summaries(self, user_id: str) -> int:
        """Count SceneSummary and MemorySummary records for the threshold."""
        return await self.implicit_memory_summary_repo.count_by_end_user_id(user_id)

    async def get_user_summaries(
        self, user_id: str, time_range: TimeRange | None = None, limit: int = 1000
    ) -> list[UserMemorySummary]:
        """Retrieve user SceneSummary and MemorySummary records from Neo4j.

        Args:
            user_id: Target user ID
            time_range: Optional time range filter
            limit: Maximum number of summaries

        Returns:
            List of user memory summaries
        """
        try:
            start_date = time_range.start_date if time_range else None
            end_date = time_range.end_date if time_range else None

            summary_dicts = await self.implicit_memory_summary_repo.find_by_end_user_id(
                end_user_id=user_id, limit=limit, start_date=start_date, end_date=end_date
            )

            summaries = []
            for summary_dict in summary_dicts:
                summary = self._dict_to_user_summary(summary_dict, user_id)
                if summary:
                    summaries.append(summary)

            logger.info(f"Retrieved {len(summaries)} summary records for user {user_id}")
            return summaries

        except Exception as e:
            logger.error(f"Failed to retrieve summaries for user {user_id}: {e}")
            raise
