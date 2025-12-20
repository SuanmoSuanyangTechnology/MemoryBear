import datetime
import uuid
from sqlalchemy import Column, String, DateTime, Boolean
from sqlalchemy.dialects.postgresql import UUID
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
    
    # Relationship to users - one tenant has many users
    users = relationship("User", back_populates="tenant")
    
    # Relationship to workspaces owned by the tenant
    owned_workspaces = relationship("Workspace", back_populates="tenant")
    
    # Relationship to tool configs owned by the tenant
    tool_configs = relationship("ToolConfig", back_populates="tenant")
