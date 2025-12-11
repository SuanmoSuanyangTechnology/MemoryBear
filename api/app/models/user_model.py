import datetime
import uuid
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.db import Base

class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    is_superuser = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.now)
    updated_at = Column(DateTime, default=datetime.datetime.now, onupdate=datetime.datetime.now)
    last_login_at = Column(DateTime, nullable=True)  # 最后登录时间，可为空
    
    current_workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=True)  # 当前工作空间ID，可为空
    
    # Foreign key to tenant - each user belongs to exactly one tenant
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    
    # Relationship to workspace memberships - users collaborate in workspaces through membership
    workspaces = relationship("WorkspaceMember", back_populates="user")
    
    # Relationship to tenant - one-to-one relationship
    tenant = relationship("Tenants", back_populates="users")
