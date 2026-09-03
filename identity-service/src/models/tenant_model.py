"""只读映射 core tenants 表；表结构归 core 管理，core 改列名/删列时须同步本文件。"""
from sqlalchemy import Boolean, Column, DateTime
from sqlalchemy.dialects.postgresql import UUID

from src.models.base import ReadOnlyBase


class Tenants(ReadOnlyBase):
    __tablename__ = "tenants"

    id = Column(UUID(as_uuid=True), primary_key=True)
    is_active = Column(Boolean, nullable=True)
    updated_at = Column(DateTime, nullable=True)
