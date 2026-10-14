import datetime
import uuid
from sqlalchemy import Column, Integer, String, DateTime, Boolean, Index, JSON
from sqlalchemy.dialects.postgresql import UUID
from .base import ServiceBase as Base
from ..utils.datetime_utils import utcnow_naive


class GenericFile(Base):
    """
    Generic file model supporting multiple upload contexts
    (avatar, app icon, knowledge-base file, temp file, etc.)
    """
    __tablename__ = "generic_files"

    # Primary key and tenant info
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True, comment="文件唯一标识")
    tenant_id = Column(UUID(as_uuid=True), nullable=False, index=True, comment="租户ID")
    created_by = Column(UUID(as_uuid=True), nullable=False, index=True, comment="创建者用户ID")

    # Basic file info
    file_name = Column(String, nullable=False, comment="原始文件名")
    file_ext = Column(String, nullable=False, index=True, comment="文件扩展名")
    file_size = Column(Integer, nullable=False, comment="文件大小（字节）")
    mime_type = Column(String, nullable=True, comment="MIME类型")

    # Upload context
    context = Column(String, nullable=False, index=True, comment="上传上下文（avatar/app_icon/knowledge_base/temp/attachment）")

    # Storage info
    storage_path = Column(String, nullable=False, comment="文件存储路径")

    # Metadata (JSON format, stores business-related info)
    file_metadata = Column(JSON, nullable=True, default={}, comment="业务元数据")

    # Status and access control
    status = Column(String, default="active", index=True, comment="文件状态（active/processing/deleted）")
    is_public = Column(Boolean, default=False, comment="是否公开访问")
    access_url = Column(String, nullable=True, comment="访问URL")

    # Reference count (used to decide whether the file can be deleted)
    reference_count = Column(Integer, default=0, comment="引用计数")

    # Timestamps
    created_at = Column(DateTime, default=utcnow_naive, comment="创建时间")
    updated_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive, comment="更新时间")
    deleted_at = Column(DateTime, nullable=True, comment="删除时间（软删除）")

    # Composite indexes
    __table_args__ = (
        Index('idx_tenant_context', 'tenant_id', 'context'),
        Index('idx_tenant_status', 'tenant_id', 'status'),
        Index('idx_created_at', 'created_at'),
    )
