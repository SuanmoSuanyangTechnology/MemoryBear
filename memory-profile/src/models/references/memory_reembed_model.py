"""Minimal read/write mappings for the re-embedding state machine.

``memory_reembed_jobs`` / ``memory_reembed_job_users`` are owned by the API
migration chain. Three callers use them here: the memory-read path asks whether an
end user's stored vectors are still being rebuilt, the workspace re-embed
status endpoints list a job's per-user rows, and the manual-retry path reopens
the job and requeues its terminal-failed rows. The mappings keep only the columns
those callers need; a wider mapping would invite them to depend on fields this
service does not own.

Like every reference mapping here, no foreign keys, no relationships, and no
participation in migration metadata.
"""

from sqlalchemy import BigInteger, Column, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID

from src.models.base import ReadOnlyBase


class MemoryReembedJobRow(ReadOnlyBase):
    __tablename__ = "memory_reembed_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True)
    workspace_id = Column(UUID(as_uuid=True), nullable=False)
    #: Slot stores ``model_configs.id`` as text; the read compares it as a string.
    new_embedding_config_id = Column(String, nullable=False)
    status = Column(String, nullable=False)
    old_model_name = Column(String, nullable=True)
    new_model_name = Column(String, nullable=True)
    total_end_users = Column(Integer, nullable=False)
    processed_end_users = Column(Integer, nullable=False)
    #: Node-level failure count; the detail payload exposes it as "damage size".
    failed_nodes = Column(BigInteger, nullable=False)
    created_at = Column(DateTime, nullable=False)
    started_at = Column(DateTime, nullable=True)
    #: Set when the job reaches a terminal state; "current job" visibility ages off it.
    finished_at = Column(DateTime, nullable=True)
    #: Last failure reason. Manual retry reopens the job and must clear it.
    error = Column(Text, nullable=True)


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
