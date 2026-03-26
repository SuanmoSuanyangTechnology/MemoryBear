import datetime
import uuid
from sqlalchemy import Column, String, DateTime, Boolean, text
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from sqlalchemy.orm import relationship
from app.db import Base


class Tenants(Base):
    __tablename__ = "tenants"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    name = Column(String, index=True, nullable=False)
    description = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.now)
    updated_at = Column(DateTime, default=datetime.datetime.now, onupdate=datetime.datetime.now)
    is_active = Column(Boolean, default=True)
    
    # SSO 外部关联字段
    external_id = Column(String(100), nullable=True, index=True)  # 外部企业ID
    external_source = Column(String(50), nullable=True)  # 来源系统
    
    # 国际化语言配置字段
    default_language = Column(String(10), nullable=False, default='zh', server_default='zh', index=True)  # 租户默认语言
    supported_languages = Column(ARRAY(String(10)), nullable=False, default=lambda: ['zh', 'en'], server_default=text("'{zh,en}'"))  # 租户支持的语言列表

    # 租户联系信息
    contact_name = Column(String(100), nullable=True)   # 联系人姓名
    contact_email = Column(String(255), nullable=True)  # 联系人邮箱
    contact_phone = Column(String(50), nullable=True)   # 联系人电话

    # 租户套餐信息
    plan = Column(String(50), nullable=True)                        # 套餐类型
    plan_expired_at = Column(DateTime, nullable=True)               # 套餐到期时间
    api_ops_rate_limit = Column(String(100), nullable=True)         # API 调用频率限制
    status = Column(String(50), nullable=True, default='active')    # 租户状态
    
    # 租户功能开关字段
    feature_billing = Column(Boolean, default=False, nullable=False, server_default='false', comment="是否启用收费管理菜单")
    feature_user_management = Column(Boolean, default=False, nullable=False, server_default='false', comment="是否启用用户管理菜单")
    
    # Relationship to users - one tenant has many users
    users = relationship("User", back_populates="tenant")
    
    # Relationship to workspaces owned by the tenant
    owned_workspaces = relationship("Workspace", back_populates="tenant")
    
    # Relationship to tool configs owned by the tenant
    tool_configs = relationship("ToolConfig", back_populates="tenant")
