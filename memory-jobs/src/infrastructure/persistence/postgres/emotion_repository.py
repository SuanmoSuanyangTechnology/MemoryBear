"""Session-bound domain persistence; transaction ownership stays in the use case."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.dialects.postgresql import insert

from src.infrastructure.persistence.postgres.task_context_repository import (
    SqlAlchemyTaskContextRepository,
)
from src.models.emotion_model import EmotionStorage


class SqlAlchemyEmotionSuggestionsRepository:
    def __init__(self, session) -> None:
        self.session = session

    def save(self, context, data) -> None:
        now = datetime.now(UTC).replace(tzinfo=None)
        session = self.session
        SqlAlchemyTaskContextRepository.guard(session, context)
        statement = insert(EmotionStorage).values(
            id=uuid4(),
            end_user_id=context["end_user_id"],
            emotion_suggestions=data,
            created_at=now,
            updated_at=now,
            emotion_generated_at=now,
        )
        session.execute(
            statement.on_conflict_do_update(
                index_elements=[EmotionStorage.end_user_id],
                set_={
                    "emotion_suggestions": data,
                    "updated_at": now,
                    "emotion_generated_at": now,
                },
            )
        )
