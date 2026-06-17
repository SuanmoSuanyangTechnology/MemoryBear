import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging_config import get_db_logger
from app.models import ConversationContextState

logger = get_db_logger()


class ContextStateRepository:
    """上下文状态仓储。"""

    def __init__(self, db: Session):
        self.db = db

    def get(
            self,
            conversation_id: uuid.UUID,
            scope_key: str,
    ) -> Optional[ConversationContextState]:
        stmt = select(ConversationContextState).where(
            ConversationContextState.conversation_id == conversation_id,
            ConversationContextState.scope_key == scope_key,
        )
        return self.db.scalars(stmt).first()

    def upsert(
            self,
            *,
            conversation_id: uuid.UUID,
            scope_key: str,
            source_type: str,
            summary_text: Optional[str] = None,
            summarized_until_message_id: Optional[uuid.UUID] = None,
            summarized_until_at=None,
            summarized_until_seq: Optional[int] = None,
    ) -> ConversationContextState:
        state = self.get(conversation_id=conversation_id, scope_key=scope_key)
        if state is None:
            state = ConversationContextState(
                conversation_id=conversation_id,
                scope_key=scope_key,
                source_type=source_type,
            )
            self.db.add(state)

        state.source_type = source_type
        state.summary_text = summary_text
        state.summarized_until_message_id = summarized_until_message_id
        state.summarized_until_at = summarized_until_at
        state.summarized_until_seq = summarized_until_seq

        logger.info(
            "Upserted context state",
            extra={
                "conversation_id": str(conversation_id),
                "scope_key": scope_key,
                "source_type": source_type,
                "has_summary": bool(summary_text),
                "summarized_until_message_id": str(summarized_until_message_id) if summarized_until_message_id else None,
                "summarized_until_seq": summarized_until_seq,
            }
        )
        return state
