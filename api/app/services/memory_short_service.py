from typing import Dict, List

from sqlalchemy.orm import Session

from app.core.logging_config import get_api_logger
from app.repositories.memory_short_repository import (
    LongTermMemoryRepository,
    ShortTermMemoryRepository,
)

api_logger = get_api_logger()


class ShortService:
    def __init__(self, end_user_id: str, db: Session) -> None:
        """Service for short-term memory queries.

        Args:
            end_user_id: The end user identifier to query memories for.
            db: SQLAlchemy database session (caller-managed lifecycle).
        """
        self.short_repo = ShortTermMemoryRepository(db)
        self.end_user_id = end_user_id

    def get_short_databasets(self) -> List[Dict]:
        """Retrieve the latest short-term memory entries for the user.

        Returns:
            List[Dict]: List of memory dicts with retrieval, message, and answer keys.
        """
        short_memories = self.short_repo.get_latest_by_user_id(self.end_user_id, 3)
        short_result = []
        for memory in short_memories:
            deep_expanded = {}
            messages = memory.messages
            aimessages = memory.aimessages
            retrieved_content = memory.retrieved_content or []

            api_logger.debug(f"Retrieved content: {retrieved_content}")

            retrieval_source = []
            for item in retrieved_content:
                if isinstance(item, dict):
                    for key, values in item.items():
                        retrieval_source.append({"query": key, "retrieval": values, "source": "上下文记忆"})

            deep_expanded['retrieval'] = retrieval_source
            deep_expanded['message'] = messages
            deep_expanded['answer'] = aimessages
            short_result.append(deep_expanded)
        return short_result

    def get_short_count(self) -> int:
        """Count total short-term memory entries for the user.

        Returns:
            int: Number of short-term memory records.
        """
        short_count = self.short_repo.count_by_user_id(self.end_user_id)
        return short_count


class LongService:
    def __init__(self, end_user_id: str, db: Session) -> None:
        """Service for long-term memory queries.

        Args:
            end_user_id: The end user identifier to query memories for.
            db: SQLAlchemy database session (caller-managed lifecycle).
        """
        self.long_repo = LongTermMemoryRepository(db)
        self.end_user_id = end_user_id

    def get_long_databasets(self) -> List[Dict]:
        """Retrieve long-term memory retrieval data for the user.

        Returns:
            List[Dict]: List of dicts with query and retrieval keys.
        """
        long_memories = self.long_repo.get_by_user_id(self.end_user_id, 1)

        long_result = []
        for long_memory in long_memories:
            if long_memory.retrieved_content:
                for memory_item in long_memory.retrieved_content:
                    if isinstance(memory_item, dict):
                        for key, values in memory_item.items():
                            long_result.append({"query": key, "retrieval": values})
        return long_result
