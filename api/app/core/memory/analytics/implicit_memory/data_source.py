"""
Memory Data Source

Handles retrieval and processing of memory data from Neo4j using direct Cypher queries.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.repositories.neo4j.memory_summary_repository import MemorySummaryRepository
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.schemas.implicit_memory_schema import TimeRange, UserMemorySummary
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class MemoryDataSource:
    """Retrieves processed memory data from Neo4j using direct Cypher queries."""
    
    def __init__(
        self,
        db: Session,
        neo4j_connector: Optional[Neo4jConnector] = None
    ):
        self.db = db
        self.neo4j_connector = neo4j_connector or Neo4jConnector()
        self.memory_summary_repo = MemorySummaryRepository(self.neo4j_connector)
    
    def _parse_timestamp(self, timestamp: Any) -> datetime:
        """Parse timestamp from various formats."""
        if isinstance(timestamp, str):
            return datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
        elif timestamp is None:
            return datetime.now()
        return timestamp
    
    def _dict_to_user_summary(self, summary_dict: Dict, user_id: str) -> Optional[UserMemorySummary]:
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
                summary_type="memory_summary"
            )
        except Exception as e:
            logger.warning(f"Failed to parse summary {summary_dict.get('id', 'unknown')}: {e}")
            return None
    
    async def get_user_summaries(
        self,
        user_id: str,
        time_range: Optional[TimeRange] = None,
        limit: int = 1000
    ) -> List[UserMemorySummary]:
        """Retrieve user memory summaries from Neo4j.
        
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
            
            summary_dicts = await self.memory_summary_repo.find_by_end_user_id(
                end_user_id=user_id,
                limit=limit,
                start_date=start_date,
                end_date=end_date
            )
            
            summaries = []
            for summary_dict in summary_dicts:
                summary = self._dict_to_user_summary(summary_dict, user_id)
                if summary:
                    summaries.append(summary)
            
            logger.info(f"Retrieved {len(summaries)} summaries for user {user_id}")
            return summaries
            
        except Exception as e:
            logger.error(f"Failed to retrieve summaries for user {user_id}: {e}")
            raise
    