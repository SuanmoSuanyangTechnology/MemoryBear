"""Session-bound domain persistence; transaction ownership stays in the use case."""

import json
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import update

from src.infrastructure.persistence.postgres.task_context_repository import (
    SqlAlchemyTaskContextRepository,
)
from src.models import EndUser


class SqlAlchemyRAGProfileRepository:
    def __init__(self, session) -> None:
        self.session = session

    def save(self, context, data) -> None:
        now = datetime.now(UTC).replace(tzinfo=None)
        session = self.session
        SqlAlchemyTaskContextRepository.guard(session, context)
        session.execute(
            update(EndUser)
            .where(EndUser.id == UUID(context["end_user_id"]))
            .values(
                user_summary=data["summary"],
                rag_tags=json.dumps(data["tags"], ensure_ascii=False),
                rag_personas=json.dumps(data["personas"], ensure_ascii=False),
                user_summary_updated_at=now,
                memory_insight=data["memory_insight"],
                behavior_pattern=data["behavior_pattern"],
                key_findings=data["key_findings"],
                growth_trajectory=data["growth_trajectory"],
                memory_insight_updated_at=now,
            )
        )
