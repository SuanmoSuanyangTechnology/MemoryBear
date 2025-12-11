import datetime
import uuid
from sqlalchemy import Column, Integer, String, DateTime, Boolean, Index, JSON
from sqlalchemy.dialects.postgresql import UUID
from app.db import Base


class GenericFile(Base):
    """
    通用文件模型，支持多种上传上下文（头像、应用图标、知识库文件、临时文件等）
    """
    __tablename__ = "generic_files"

    # 主键和租户信息
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True, comment="文件唯一标识")
    tenant_id = Column(UUID(as_uuid=True), nullable=False, index=True, comment="租户ID")
    created_by = Column(UUID(as_uuid=True), nullable=False, index=True, comment="创建者用户ID")

    # 文件基本信息
    file_name = Column(String, nullable=False, comment="原始文件名")
    file_ext = Column(String, nullable=False, index=True, comment="文件扩展名")
    file_size = Column(Integer, nullable=False, comment="文件大小（字节）")
    mime_type = Column(String, nullable=True, comment="MIME类型")

    # 上传上下文
    context = Column(String, nullable=False, index=True, comment="上传上下文（avatar/app_icon/knowledge_base/temp/attachment）")

    # 存储信息
    storage_path = Column(String, nullable=False, comment="文件存储路径")

    # 元数据（JSON格式，存储业务相关信息）
    file_metadata = Column(JSON, nullable=True, default={}, comment="业务元数据")

    # 状态和访问控制
    status = Column(String, default="active", index=True, comment="文件状态（active/processing/deleted）")
    is_public = Column(Boolean, default=False, comment="是否公开访问")
    access_url = Column(String, nullable=True, comment="访问URL")

    # 引用计数（用于判断文件是否可以删除）
    reference_count = Column(Integer, default=0, comment="引用计数")

    # 时间戳
    created_at = Column(DateTime, default=datetime.datetime.now, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.datetime.now, onupdate=datetime.datetime.now, comment="更新时间")
    deleted_at = Column(DateTime, nullable=True, comment="删除时间（软删除）")

    # 复合索引
    __table_args__ = (
        Index('idx_tenant_context', 'tenant_id', 'context'),
        Index('idx_tenant_status', 'tenant_id', 'status'),
        Index('idx_created_at', 'created_at'),
    )
