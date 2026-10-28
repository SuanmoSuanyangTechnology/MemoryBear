"""AI personality items owned by memory-profile."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, MetaData, String, Table, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from src.models.base import ProfileBase

# Resolve shared-table foreign keys without registering shared tables as owned.
_shared_metadata = MetaData()
_workspaces = Table(
    "workspaces", _shared_metadata, Column("id", UUID(as_uuid=True), primary_key=True)
)
_end_users = Table(
    "end_users", _shared_metadata, Column("id", UUID(as_uuid=True), primary_key=True)
)


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class AiPersonality(ProfileBase):
    __tablename__ = "ai_personality"
    __table_args__ = (
        Index(
            "ix_ai_personality_workspace_user_status_created",
            "workspace_id",
            "end_user_id",
            "status",
            "created_at",
            "item_id",
        ),
    )

    item_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, comment="人格条目ID")
    workspace_id = Column(
        UUID(as_uuid=True), ForeignKey(_workspaces.c.id), nullable=False, comment="所属工作空间ID"
    )
    end_user_id = Column(
        UUID(as_uuid=True), ForeignKey(_end_users.c.id), nullable=False, comment="所属终端用户ID"
    )
    title = Column(Text, nullable=False, comment="人格标题")
    dimension = Column(String(24), nullable=False, comment="人格六维分类")
    rule = Column(Text, nullable=False, comment="AI相处规则")
    scope = Column(Text, nullable=False, comment="适用范围")
    reason = Column(Text, nullable=False, comment="形成依据")
    status = Column(
        String(16),
        nullable=False,
        default="active",
        server_default="active",
        comment="active/disabled",
    )
    sources = Column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
        comment="来源field/content数组",
    )
    created_at = Column(
        DateTime,
        nullable=False,
        default=_utcnow_naive,
        server_default=text("timezone('UTC', CURRENT_TIMESTAMP)"),
        comment="正式条目入库时间（UTC）",
    )
    updated_at = Column(
        DateTime,
        nullable=False,
        default=_utcnow_naive,
        onupdate=_utcnow_naive,
        server_default=text("timezone('UTC', CURRENT_TIMESTAMP)"),
        comment="最近实际修改时间（UTC）",
    )
