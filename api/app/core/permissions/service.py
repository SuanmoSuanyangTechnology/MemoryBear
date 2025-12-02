"""
Unified permission service for centralized access control.

This service provides a single point for all permission checks in the application,
replacing scattered inline permission logic.
"""

from typing import List, Optional
from app.core.permissions.models import Subject, Resource, Action
from app.core.permissions.policies import (
    PermissionPolicy,
    SuperuserPolicy,
    OwnerPolicy,
    TenantPolicy,
    SelfAccessPolicy,
)
from app.core.exceptions import PermissionDeniedException
from app.core.logging_config import get_logger

logger = get_logger(__name__)


class PermissionService:
    """
    Centralized permission service.
    
    Uses a chain of permission policies to determine if an action is allowed.
    Any policy in the chain can grant permission (OR logic).
    """
    
    def __init__(self):
        # Default policy chain - order matters for performance
        # Most common/permissive policies first
        self.policies: List[PermissionPolicy] = [
            SuperuserPolicy(),  # Check superuser first (most common bypass)
            OwnerPolicy(),      # Then check ownership
            SelfAccessPolicy(), # Then self-access for user resources
            TenantPolicy(),     # Finally tenant-level access
        ]
    
    def add_policy(self, policy: PermissionPolicy, position: Optional[int] = None):
        """
        Add a permission policy to the chain.
        
        Args:
            policy: The policy to add
            position: Optional position in the chain (default: append to end)
        """
        if position is not None:
            self.policies.insert(position, policy)
        else:
            self.policies.append(policy)
    
    def remove_policy(self, policy_class: type):
        """
        Remove all policies of a specific class from the chain.
        
        Args:
            policy_class: The class of policies to remove
        """
        self.policies = [p for p in self.policies if not isinstance(p, policy_class)]
    
    def can_perform(
        self,
        subject: Subject,
        action: Action,
        resource: Resource
    ) -> bool:
        """
        Check if a subject can perform an action on a resource.
        
        Args:
            subject: The user/actor attempting the action
            action: The action being attempted
            resource: The resource being acted upon
            
        Returns:
            True if any policy grants permission, False otherwise
        """
        # Policy chain: any policy can grant permission (OR logic)
        for policy in self.policies:
            try:
                if policy.can_perform(subject, action, resource):
                    logger.debug(
                        f"permission_granted: policy={policy.__class__.__name__}, "
                        f"subject_id={subject.id}, action={action.value}, "
                        f"resource_type={resource.type.value}, resource_id={resource.id}"
                    )
                    return True
            except Exception as e:
                # Log policy errors but continue checking other policies
                logger.error(
                    f"permission_policy_error: policy={policy.__class__.__name__}, "
                    f"error={str(e)}, subject_id={subject.id}, action={action.value}, "
                    f"resource_type={resource.type.value}"
                )
        
        logger.warning(
            f"permission_denied: subject_id={subject.id}, action={action.value}, "
            f"resource_type={resource.type.value}, resource_id={resource.id}, "
            f"subject_tenant={subject.tenant_id}, resource_tenant={resource.tenant_id}, "
            f"is_superuser={subject.is_superuser}"
        )
        return False
    
    def require_permission(
        self,
        subject: Subject,
        action: Action,
        resource: Resource,
        error_message: Optional[str] = None
    ):
        """
        Require permission, raising an exception if not granted.
        
        Args:
            subject: The user/actor attempting the action
            action: The action being attempted
            resource: The resource being acted upon
            error_message: Custom error message (optional)
            
        Raises:
            PermissionDeniedException: If permission is not granted
        """
        if not self.can_perform(subject, action, resource):
            message = error_message or (
                f"无权对 {resource.type.value} 执行 {action.value} 操作"
            )
            raise PermissionDeniedException(message)
    
    def check_superuser(self, subject: Subject, error_message: Optional[str] = None):
        """
        Require that the subject is a superuser.
        
        Args:
            subject: The user/actor to check
            error_message: Custom error message (optional)
            
        Raises:
            PermissionDeniedException: If subject is not a superuser
        """
        if not subject.is_superuser:
            message = error_message or "需要超级管理员权限"
            logger.warning(
                f"superuser_required: subject_id={subject.id}, is_superuser={subject.is_superuser}"
            )
            raise PermissionDeniedException(message)
    
    def check_same_tenant(
        self,
        subject: Subject,
        resource: Resource,
        error_message: Optional[str] = None
    ):
        """
        Require that the subject and resource are in the same tenant.
        
        Args:
            subject: The user/actor to check
            resource: The resource to check
            error_message: Custom error message (optional)
            
        Raises:
            PermissionDeniedException: If not in the same tenant
        """
        if subject.tenant_id != resource.tenant_id:
            message = error_message or "无权访问其他租户的资源"
            logger.warning(
                f"tenant_mismatch: subject_id={subject.id}, "
                f"subject_tenant={subject.tenant_id}, resource_tenant={resource.tenant_id}"
            )
            raise PermissionDeniedException(message)


# Global permission service instance
permission_service = PermissionService()
