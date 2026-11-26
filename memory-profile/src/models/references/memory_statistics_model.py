"""Read-only mappings for API-owned memory statistics tables."""

from sqlalchemy import JSON, Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from src.models.base import ReadOnlyBase


class MemoryMessage(ReadOnlyBase):
    """The API's ``memory_messages`` row.

    ``ReadOnlyBase`` names who owns the DDL, not the access mode: this service
    inserts and updates rows here for the write faces while the API keeps the
    table's schema and its workers consume what is written. Every column the
    three write entry points need is mapped, so no other module has to declare
    the table a second time.
    """

    __tablename__ = "memory_messages"

    id = Column(UUID(as_uuid=True), primary_key=True)
    conversation_id = Column(UUID(as_uuid=True), nullable=True)
    original_message_id = Column(UUID(as_uuid=True), nullable=True)
    end_user_id = Column(String, nullable=False)
    source = Column(String(32), nullable=False)
    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)
    message_seq = Column(Integer, nullable=False)
    should_memorize = Column(Boolean, nullable=False, default=True)
    dialog_at = Column(String(64), nullable=True)
    files = Column(JSON, nullable=True)
    pruned_content = Column(Text, nullable=True)
    topic_entity_hint = Column(String(512), nullable=True)
    scene_boundary = Column(String(32), nullable=True)
    scene_summary_claimed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=True)


class MemoryPerceptual(ReadOnlyBase):
    __tablename__ = "memory_perceptual"

    id = Column(UUID(as_uuid=True), primary_key=True)
    end_user_id = Column(UUID(as_uuid=True), nullable=True)
    perceptual_type = Column(Integer, nullable=False)
    storage_service = Column(Integer, nullable=True)
    file_path = Column(String, nullable=False)
    file_name = Column(String, nullable=False)
    file_ext = Column(String, nullable=False)
    summary = Column(String, nullable=True)
    meta_data = Column(JSONB, nullable=True)
    created_time = Column(DateTime, nullable=True)


class ShortTermMemory(ReadOnlyBase):
    __tablename__ = "memory_short_term"

    id = Column(UUID(as_uuid=True), primary_key=True)
    end_user_id = Column(String(255), nullable=False)
    messages = Column(Text, nullable=False)
    aimessages = Column(Text, nullable=True)
    search_switch = Column(String(50), nullable=True)
    retrieved_content = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False)


class LongTermMemory(ReadOnlyBase):
    """``memory_long_term``: only the merge's reference reassignment needs it."""

    __tablename__ = "memory_long_term"

    id = Column(UUID(as_uuid=True), primary_key=True)
    end_user_id = Column(String(255), nullable=False)
    retrieved_content = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False)


class ForgetAudit(ReadOnlyBase):
    """One forgotten-memory audit row (``memory_forget_log``).

    The profile statistics module only counts rows, while the migrated forget
    endpoints also page and aggregate them; the full column set is mapped so one
    class serves both without a second mapping of the same table.
    """

    __tablename__ = "memory_forget_log"

    id = Column(UUID(as_uuid=True), primary_key=True)
    end_user_id = Column(UUID(as_uuid=True), nullable=False)
    node_id = Column(String, nullable=False)
    node_type = Column(String, nullable=False)
    content = Column(String, nullable=False)
    trigger = Column(Integer, nullable=False)
    reason = Column(String, nullable=False)
    recoverable = Column(Boolean, nullable=False)
    operator = Column(UUID(as_uuid=True), nullable=True)
    delete_at = Column(DateTime, nullable=False)
    is_recovered = Column(Boolean, nullable=False)



class MemoryIncrement(ReadOnlyBase):
    __tablename__ = "memory_increments"

    id = Column(UUID(as_uuid=True), primary_key=True)
    workspace_id = Column(UUID(as_uuid=True), nullable=False)
    total_num = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=True)


class DialogueEmotionRaw(ReadOnlyBase):
    __tablename__ = "dialogue_emotion_raw"

    id = Column(UUID(as_uuid=True), primary_key=True)
    dialogue_id = Column(String(255), nullable=False)
    end_user_id = Column(UUID(as_uuid=True), nullable=False)
    created_at = Column(DateTime, nullable=False)
    emotion = Column(String(50), nullable=False)
