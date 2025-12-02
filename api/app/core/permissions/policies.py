"""
Permission policies for access control.

Defines various policy classes that implement different permission rules:
- SuperuserPolicy: Superusers can perform any action
- OwnerPolicy: Resource owners can perform any action on their resources
- TenantPolicy: Users in the same tenant can access public resources
- RoleBasedPolicy: Permission based on user roles
- WorkspaceMemberPolicy: Workspace members can access workspace resources
"""

from abc import ABC, abstractmethod
from typing import Set
from app.core.permissions.models import Subject, Resource, Action, ResourceType


class PermissionPolicy(ABC):
    """Base class for permission policies."""
    
    @abstractmethod
    def can_perform(self, subject: Subject, action: Action, resource: Resource) -> bool:
        """
        Determine if a subject can perform an action on a resource.
        
        Args:
            subject: The user/actor attempting the action
            action: The action being attempted
            resource: The resource being acted upon
            
        Returns:
            True if the action is allowed, False otherwise
        """
        pass


class SuperuserPolicy(PermissionPolicy):
    """Superusers can perform any action on any resource."""
    
    def can_perform(self, subject: Subject, action: Action, resource: Resource) -> bool:
        return subject.is_superuser


class OwnerPolicy(PermissionPolicy):
    """Resource owners can perform any action on their own resources."""
    
    def can_perform(self, subject: Subject, action: Action, resource: Resource) -> bool:
        return subject.id == resource.owner_id


class TenantPolicy(PermissionPolicy):
    """
    Users in the same tenant can access public resources.
    
    Args:
        allowed_actions: Set of actions allowed on public resources (default: READ only)
    """
    
    def __init__(self, allowed_actions: Set[Action] = None):
        self.allowed_actions = allowed_actions or {Action.READ}
    
    def can_perform(self, subject: Subject, action: Action, resource: Resource) -> bool:
        return (
            subject.tenant_id == resource.tenant_id and
            resource.is_public and
            action in self.allowed_actions
        )


class RoleBasedPolicy(PermissionPolicy):
    """
    Permission based on user roles.
    
    Args:
        required_roles: Set of roles that grant permission
        allowed_actions: Set of actions these roles can perform
    """
    
    def __init__(self, required_roles: Set[str], allowed_actions: Set[Action]):
        self.required_roles = required_roles
        self.allowed_actions = allowed_actions
    
    def can_perform(self, subject: Subject, action: Action, resource: Resource) -> bool:
        has_role = bool(subject.roles & self.required_roles)
        return has_role and action in self.allowed_actions


class WorkspaceMemberPolicy(PermissionPolicy):
    """
    Workspace members can access workspace resources.
    
    Args:
        allowed_actions: Set of actions workspace members can perform
    """
    
    def __init__(self, allowed_actions: Set[Action] = None):
        self.allowed_actions = allowed_actions or {Action.READ, Action.UPDATE}
    
    def can_perform(self, subject: Subject, action: Action, resource: Resource) -> bool:
        if resource.type != ResourceType.WORKSPACE:
            return False
        
        return (
            resource.id in subject.workspace_memberships and
            action in self.allowed_actions
        )


class SameTenantSuperuserPolicy(PermissionPolicy):
    """
    Superusers in the same tenant can perform specific actions.
    
    This is useful for tenant-scoped admin operations where even superusers
    should be limited to their own tenant.
    
    Args:
        allowed_actions: Set of actions allowed (default: all actions)
    """
    
    def __init__(self, allowed_actions: Set[Action] = None):
        self.allowed_actions = allowed_actions or set(Action)
    
    def can_perform(self, subject: Subject, action: Action, resource: Resource) -> bool:
        return (
            subject.is_superuser and
            subject.tenant_id == resource.tenant_id and
            action in self.allowed_actions
        )


class SelfAccessPolicy(PermissionPolicy):
    """
    Users can access their own user resource.
    
    This is specifically for user resources where users should be able
    to read/update their own profile.
    
    Args:
        allowed_actions: Set of actions users can perform on themselves
    """
    
    def __init__(self, allowed_actions: Set[Action] = None):
        self.allowed_actions = allowed_actions or {Action.READ, Action.UPDATE}
    
    def can_perform(self, subject: Subject, action: Action, resource: Resource) -> bool:
        if resource.type != ResourceType.USER:
            return False
        
        return (
            subject.id == resource.id and
            action in self.allowed_actions
        )
