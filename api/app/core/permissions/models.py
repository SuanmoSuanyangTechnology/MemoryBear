"""
Permission models for access control.

Defines the core models used in the permission system:
- Action: Types of operations that can be performed
- ResourceType: Types of resources in the system
- Resource: Represents a resource with ownership and tenant information
- Subject: Represents a user/actor performing an action
"""

from enum import Enum
from typing import Set, Optional
from dataclasses import dataclass, field
from uuid import UUID


class Action(Enum):
    """Operation types that can be performed on resources."""
    CREATE = "create"
    READ = "read"
    UPDATE = "update"
    DELETE = "delete"
    SHARE = "share"
    MANAGE = "manage"
    ACTIVATE = "activate"
    DEACTIVATE = "deactivate"


class ResourceType(Enum):
    """Types of resources in the system."""
    FILE = "file"
    WORKSPACE = "workspace"
    KNOWLEDGE = "knowledge"
    APP = "app"
    USER = "user"
    DOCUMENT = "document"
    MODEL = "model"
    CHUNK = "chunk"


@dataclass
class Resource:
    """
    Represents a resource in the system.
    
    Attributes:
        type: The type of resource
        id: Unique identifier of the resource
        owner_id: ID of the user who owns the resource
        tenant_id: ID of the tenant the resource belongs to
        is_public: Whether the resource is publicly accessible within the tenant
        metadata: Additional resource-specific metadata
    """
    type: ResourceType
    id: UUID
    owner_id: UUID
    tenant_id: UUID
    is_public: bool = False
    metadata: dict = field(default_factory=dict)
    
    @classmethod
    def from_file(cls, file_obj) -> "Resource":
        """Create a Resource from a GenericFile model instance."""
        return cls(
            type=ResourceType.FILE,
            id=file_obj.id,
            owner_id=file_obj.created_by,
            tenant_id=file_obj.tenant_id,
            is_public=getattr(file_obj, 'is_public', False),
            metadata={
                "file_name": file_obj.file_name,
                "context": file_obj.context,
            }
        )
    
    @classmethod
    def from_workspace(cls, workspace_obj) -> "Resource":
        """Create a Resource from a Workspace model instance."""
        return cls(
            type=ResourceType.WORKSPACE,
            id=workspace_obj.id,
            owner_id=workspace_obj.tenant_id,
            tenant_id=workspace_obj.tenant_id,
            is_public=False,
            metadata={
                "name": workspace_obj.name,
            }
        )
    
    @classmethod
    def from_user(cls, user_obj) -> "Resource":
        """Create a Resource from a User model instance."""
        return cls(
            type=ResourceType.USER,
            id=user_obj.id,
            owner_id=user_obj.id,  # User owns themselves
            tenant_id=user_obj.tenant_id,
            is_public=False,
            metadata={
                "username": user_obj.username,
                "is_superuser": user_obj.is_superuser,
            }
        )


@dataclass
class Subject:
    """
    Represents a user/actor performing an action.
    
    Attributes:
        id: User ID
        tenant_id: Tenant ID the user belongs to
        is_superuser: Whether the user is a superuser
        roles: Set of role names the user has
        workspace_memberships: Set of workspace IDs the user is a member of
    """
    id: UUID
    tenant_id: UUID
    is_superuser: bool = False
    roles: Set[str] = field(default_factory=set)
    workspace_memberships: Set[UUID] = field(default_factory=set)
    
    @classmethod
    def from_user(cls, user_obj, workspace_memberships: Optional[Set[UUID]] = None) -> "Subject":
        """Create a Subject from a User model instance."""
        return cls(
            id=user_obj.id,
            tenant_id=user_obj.tenant_id,
            is_superuser=user_obj.is_superuser,
            roles=set(getattr(user_obj, 'roles', [])),
            workspace_memberships=workspace_memberships or set()
        )
