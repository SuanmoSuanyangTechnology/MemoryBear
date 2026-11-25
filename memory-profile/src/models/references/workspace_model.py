"""Read-only mapping for the API-owned workspace table."""

from sqlalchemy import Boolean, Column, String
from sqlalchemy.dialects.postgresql import UUID

from src.models.base import ReadOnlyBase


class Workspace(ReadOnlyBase):
    __tablename__ = "workspaces"

    id = Column(UUID(as_uuid=True), primary_key=True)
    name = Column(String, nullable=False)
    tenant_id = Column(UUID(as_uuid=True), nullable=False)
    # 与 api 模型一致；DDL 实际允许 NULL，业务层仍保留 NULL 兜底。
    storage_type = Column(String, nullable=False)
    is_active = Column(Boolean, nullable=True)
    is_default_config = Column(Boolean, nullable=False)
    memory_config = Column(UUID(as_uuid=True), nullable=True)
    #: 「模型选择」六列由 api 侧维护；生成类用例只读 ``llm``（默认配置空间改用 preset）。
    llm = Column(String, nullable=True)
    embedding = Column(String, nullable=True)
    rerank = Column(String, nullable=True)
    vision = Column(String, nullable=True)
    audio = Column(String, nullable=True)
    video = Column(String, nullable=True)
