import uuid
from sqlalchemy import Column, Integer, String, DateTime, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from app.db import Base
from app.core.utils.datetime_utils import utcnow_naive


class KnowledgeMetadata(Base):
    __tablename__ = "knowledge_metadatas"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=False, comment="租户ID")
    knowledge_id = Column(UUID(as_uuid=True), nullable=False, index=True, comment="知识库ID")
    type = Column(String, nullable=False, comment="字段类型: string | number | time")
    name = Column(String(255), nullable=False, comment="字段名")
    created_by = Column(UUID(as_uuid=True), comment="创建人")
    updated_by = Column(UUID(as_uuid=True), comment="更新人")
    created_at = Column(DateTime, default=utcnow_naive)
    updated_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)

    __table_args__ = (
        UniqueConstraint('knowledge_id', 'name', name='uq_knowledge_metadata_name'),
    )


class KnowledgeMetadataBinding(Base):
    __tablename__ = "knowledge_metadata_bindings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=False, comment="租户ID")
    knowledge_id = Column(UUID(as_uuid=True), nullable=False, index=True, comment="知识库ID")
    metadata_id = Column(UUID(as_uuid=True), nullable=False, comment="元数据定义ID")
    document_id = Column(UUID(as_uuid=True), nullable=False, comment="文档ID")
    created_by = Column(UUID(as_uuid=True), comment="创建人")
    created_at = Column(DateTime, default=utcnow_naive)

    __table_args__ = (
        UniqueConstraint('knowledge_id', 'metadata_id', 'document_id', name='uq_knowledge_metadata_binding'),
    )
