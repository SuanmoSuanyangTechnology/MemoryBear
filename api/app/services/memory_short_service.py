from typing import Dict, List

from sqlalchemy.orm import Session

from app.core.logging_config import get_api_logger
from app.models.conversation_model import Conversation
from app.models.memory_message_model import MemoryMessage
from app.repositories.memory_short_repository import (
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
    """Service for querying messages that have NOT yet been written to memory.

    Reads from memory_messages table joined with conversations to find messages
    where message_seq > write_cursor, i.e. messages waiting to be processed by
    WritePipeline. This mirrors the logic in HistorySearchService.run().

    These messages represent content that should be stored in long-term memory
    but hasn't been processed yet, presented as candidates in the long-term pool.
    """

    _ROLE_LABELS = {"user": "用户", "assistant": "助手", "system": "系统"}

    def __init__(self, end_user_id: str, db: Session) -> None:
        """Service for long-term memory queries.

        Args:
            end_user_id: The end user identifier to query messages for.
            db: SQLAlchemy database session (caller-managed lifecycle).
        """
        self.db = db
        self.end_user_id = end_user_id

    def get_long_databasets(self) -> List[Dict]:
        """Retrieve memory_messages that are pending write (message_seq > write_cursor).

        Queries all conversations for the user and collects messages where
        message_seq exceeds the conversation's write_cursor, indicating they
        haven't been processed by WritePipeline yet.

        Returns:
            List[Dict]: List of dicts with query (title/preview) and
                        retrieval (full message content) keys.
        """
        conversations = (
            self.db.query(Conversation)
            .filter(Conversation.user_id == self.end_user_id)
            .order_by(Conversation.updated_at.desc())
            .all()
        )

        long_result = []
        for conv in conversations:
            cursor = conv.write_cursor or 0
            messages = (
                self.db.query(MemoryMessage)
                .filter(
                    MemoryMessage.conversation_id == conv.id,
                    MemoryMessage.message_seq > cursor,
                )
                .order_by(MemoryMessage.message_seq.asc())
                .all()
            )

            if not messages:
                continue

            # Build a single entry per conversation: grouped messages
            lines = []
            for msg in messages:
                role_label = self._ROLE_LABELS.get(msg.role, msg.role)
                lines.append(f"[{role_label}] {msg.content}")

            retrieval = "\n\n".join(lines)
            # Use the first user message content as the query/title
            first_user = next(
                (m for m in messages if m.role == "user"), messages[0]
            )
            preview = first_user.content[:80] + ("..." if len(first_user.content) > 80 else "")
            query = f"[会话] {preview}"

            long_result.append({
                "query": query,
                "retrieval": retrieval,
            })

        return long_result
