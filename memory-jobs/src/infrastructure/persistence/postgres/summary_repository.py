"""SQLAlchemy implementation of the user-summary aggregate."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Session

from src.domain.profile.model import UserSummary, WriteOutcome
from src.models import EndUser


class SqlAlchemyUserSummaryRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, summary: UserSummary) -> WriteOutcome:
        sections = summary.sections
        result = self._session.execute(
            sa.update(EndUser)
            .where(
                EndUser.id == summary.end_user_id.value,
                EndUser.is_active.is_(True),
            )
            .values(
                user_summary=sections.basic_introduction,
                personality_traits=sections.personality,
                core_values=sections.core_values,
                one_sentence_summary=sections.one_sentence,
                user_summary_updated_at=summary.generated_at,
            )
            .execution_options(synchronize_session=False)
        )
        return WriteOutcome.UPDATED if result.rowcount else WriteOutcome.NOT_FOUND
