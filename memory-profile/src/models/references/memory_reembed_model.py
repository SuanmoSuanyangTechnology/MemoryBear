"""Minimal read-only mappings for the re-embedding state machine.

``memory_reembed_jobs`` / ``memory_reembed_job_users`` are owned by the API
migration chain. Two callers read them here: the memory-read path asks whether an
end user's stored vectors are still being rebuilt, and the workspace re-embed
status endpoints list a job's per-user rows. The mappings keep only the columns
those readers need; a wider mapping would invite readers to depend on fields this
service does not own.

Like every reference mapping here, no foreign keys, no relationships, and no
participation in migration metadata.
"""

from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID

from src.models.base import ReadOnlyBase


class MemoryReembedJobRow(ReadOnlyBase):
    __tablename__ = "memory_reembed_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True)
    workspace_id = Column(UUID(as_uuid=True), nullable=False)
    #: Slot stores ``model_configs.id`` as text; the read compares it as a string.
    new_embedding_config_id = Column(String, nullable=False)
    status = Column(String, nullable=False)
    created_at = Column(DateTime, nullable=False)
    #: Set when the job reaches a terminal state; "current job" visibility ages off it.
    finished_at = Column(DateTime, nullable=True)


class MemoryReembedJobUserRow(ReadOnlyBase):
    __tablename__ = "memory_reembed_job_users"

    id = Column(UUID(as_uuid=True), primary_key=True)
    job_id = Column(UUID(as_uuid=True), nullable=False)
    end_user_id = Column(String, nullable=False)
    status = Column(String, nullable=False)
    #: Retry budget spent so far; terminal failure is decided by this plus status.
    attempts = Column(Integer, nullable=False)
    total_nodes = Column(Integer, nullable=False)
    processed_nodes = Column(Integer, nullable=False)
    failed_nodes = Column(Integer, nullable=False)
    last_error = Column(Text, nullable=True)
    #: Renewed on every page; a stale ``running`` row means its worker died.
    updated_at = Column(DateTime, nullable=False)
