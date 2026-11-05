import uuid

from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, false, text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.utils.datetime_utils import utcnow_naive
from app.db import Base


class AIPersonalityEntry(Base):
    __tablename__ = "ai_personality_entries"
    __table_args__ = (
        CheckConstraint(
            "dimension IN ('expression', 'judgment', 'collaboration', "
            "'emotion_response', 'relationship_boundary', 'commitment_habit')",
            name="ck_ai_personality_entries_dimension",
        ),
        CheckConstraint("char_length(word) BETWEEN 1 AND 10", name="ck_ai_personality_entries_word_length"),
        UniqueConstraint("end_user_id", "dimension", "word", name="uq_ai_personality_entries_user_dimension_word"),
        Index(
            "ix_ai_personality_entries_active_user_dimension",
            "end_user_id",
            "dimension",
            postgresql_where=text("is_delete = false"),
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True), nullable=False)
    end_user_id = Column(UUID(as_uuid=True), ForeignKey("end_users.id"), nullable=False)
    dimension = Column(String(32), nullable=False)
    word = Column(String(10), nullable=False)
    entry_id = Column(UUID(as_uuid=True), nullable=False)
    ai_interaction_rule = Column(Text, nullable=False)
    applicable_scope = Column(Text, nullable=False)
    formation_basis = Column(Text, nullable=False)
    user_preferences = Column(JSONB, nullable=False)
    evidence = Column(JSONB, nullable=False)
    source_batch_id = Column(String(128), nullable=False)
    updated_at = Column(DateTime, nullable=False, default=utcnow_naive, onupdate=utcnow_naive)
    is_delete = Column(Boolean, nullable=False, default=False, server_default=false())
