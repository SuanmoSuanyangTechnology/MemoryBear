"""Permission models and policy chain for the management plane (sync).

Minimal port of ``core/api/app/core/permissions``: only the default chain
(Superuser → Owner → SelfAccess → Tenant) the migrated users routes exercise.
``RoleBasedPolicy`` / ``WorkspaceMemberPolicy`` / ``SameTenantSuperuserPolicy``
are intentionally omitted: workspace member authorization lives in
``src.core.deps`` and no migrated route needs the others. Restoring them later
is an append-only change.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from uuid import UUID

from src.core.exceptions import PermissionDeniedException

logger = logging.getLogger(__name__)


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
    """A resource with ownership and tenant context."""

    type: ResourceType
    id: UUID
    owner_id: UUID
    tenant_id: UUID
    is_public: bool = False
    metadata: dict = field(default_factory=dict)

    @classmethod
    def from_user(cls, user_obj) -> Resource:
        return cls(
            type=ResourceType.USER,
            id=user_obj.id,
            owner_id=user_obj.id,  # a user owns themselves
            tenant_id=user_obj.tenant_id,
            is_public=False,
            metadata={
                "username": user_obj.username,
                "is_superuser": user_obj.is_superuser,
            },
        )


@dataclass
class Subject:
    """The actor performing an action."""

    id: UUID
    tenant_id: UUID
    is_superuser: bool = False

    @classmethod
    def from_user(cls, user_obj) -> Subject:
        return cls(
            id=user_obj.id,
            tenant_id=user_obj.tenant_id,
            is_superuser=user_obj.is_superuser,
        )


class PermissionPolicy(ABC):
    """One rule in the permission chain."""

    @abstractmethod
    def can_perform(self, subject: Subject, action: Action, resource: Resource) -> bool:
        """Return True when this policy grants the action."""


class SuperuserPolicy(PermissionPolicy):
    """Superusers can perform any action on any resource."""

    def can_perform(self, subject: Subject, action: Action, resource: Resource) -> bool:
        return subject.is_superuser


class OwnerPolicy(PermissionPolicy):
    """Resource owners can perform any action on their own resources.

    A user owns themselves (``Resource.from_user``), so the host chain lets any
    user deactivate their own account. Kept for wire parity.
    """

    def can_perform(self, subject: Subject, action: Action, resource: Resource) -> bool:
        return subject.id == resource.owner_id


class TenantPolicy(PermissionPolicy):
    """Same-tenant access to public resources (READ-only by default)."""

    def __init__(self, allowed_actions: set[Action] | None = None) -> None:
        self.allowed_actions = allowed_actions or {Action.READ}

    def can_perform(self, subject: Subject, action: Action, resource: Resource) -> bool:
        return (
            subject.tenant_id == resource.tenant_id
            and resource.is_public
            and action in self.allowed_actions
        )


class SelfAccessPolicy(PermissionPolicy):
    """Users can read/update their own user resource."""

    def __init__(self, allowed_actions: set[Action] | None = None) -> None:
        self.allowed_actions = allowed_actions or {Action.READ, Action.UPDATE}

    def can_perform(self, subject: Subject, action: Action, resource: Resource) -> bool:
        if resource.type != ResourceType.USER:
            return False
        return subject.id == resource.id and action in self.allowed_actions


class PermissionService:
    """Policy chain: any policy granting access wins (OR logic)."""

    def __init__(self) -> None:
        self.policies: list[PermissionPolicy] = [
            SuperuserPolicy(),
            OwnerPolicy(),
            SelfAccessPolicy(),
            TenantPolicy(),
        ]

    def can_perform(self, subject: Subject, action: Action, resource: Resource) -> bool:
        for policy in self.policies:
            try:
                if policy.can_perform(subject, action, resource):
                    return True
            except Exception:
                logger.exception(
                    "permission policy failed: policy=%s subject_id=%s action=%s",
                    type(policy).__name__,
                    subject.id,
                    action.value,
                )
        return False

    def require_permission(
        self,
        subject: Subject,
        action: Action,
        resource: Resource,
        error_message: str | None = None,
    ) -> None:
        if not self.can_perform(subject, action, resource):
            message = error_message or (
                f"无权对 {resource.type.value} 执行 {action.value} 操作"
            )
            raise PermissionDeniedException(message)

    def check_superuser(self, subject: Subject, error_message: str | None = None) -> None:
        if not subject.is_superuser:
            logger.warning("superuser required: subject_id=%s", subject.id)
            raise PermissionDeniedException(error_message or "需要超级管理员权限")


permission_service = PermissionService()
