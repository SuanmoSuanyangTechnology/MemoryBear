import datetime
import uuid
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from .cross_domain_base import CrossDomainBase as Base
from ..utils.datetime_utils import utcnow_naive

class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    username = Column(String, index=True, nullable=False)  # Community edition: usernames are not unique; only email is unique
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    is_superuser = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=utcnow_naive)
    updated_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)
    last_login_at = Column(DateTime, nullable=True)  # Last login time; nullable
    
    # SSO external association fields
    external_id = Column(String(100), nullable=True)  # External user ID
    external_source = Column(String(50), nullable=True)  # Source system
    
    # User contact info
    phone = Column(String(50), nullable=True)  # User phone
    
    # User language preference
    preferred_language = Column(String(10), server_default=text("'zh'"), default='zh', nullable=False, index=True)  # Preferred language of the user; defaults to Chinese
    
    current_workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=True)  # Current workspace ID; nullable
    
    # Foreign key to tenant - each user belongs to exactly one tenant
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    
    # Relationship to workspace memberships - users collaborate in workspaces through membership
    workspaces = relationship("WorkspaceMember", back_populates="user")
    
    # Relationship to tenant - one-to-one relationship
    # Cross-domain relationship omitted: target model is host-owned and not part of this service's model set (FK column retained).
