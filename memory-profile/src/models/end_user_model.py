import uuid

from sqlalchemy import Boolean, Column, String
from sqlalchemy.dialects.postgresql import UUID

from src.infrastructure.database.base import ReadOnlyBase


class EndUser(ReadOnlyBase):
    __tablename__ = "end_users"

    id = Column(UUID(as_uuid=True), primary_key=True)
    workspace_id = Column(UUID(as_uuid=True), nullable=False)
    is_active = Column(Boolean, nullable=False)


class EndUserMerge(ReadOnlyBase):
    __tablename__ = "end_user_merge"

    id = Column(UUID(as_uuid=True), primary_key=True)
    workspace_id = Column(UUID(as_uuid=True), nullable=False)
    origin_id = Column(UUID(as_uuid=True), nullable=True)   # 原始 end_user_id
    origin_other_id = Column(String, nullable=False)        # 原始 end_user other_id
    target_id = Column(UUID(as_uuid=True), nullable=False)  # 合并目标 end_user_id
