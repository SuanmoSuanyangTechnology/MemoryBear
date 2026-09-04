"""策略引擎（语义对齐 core/app/core/permissions，唯一真源在 core，升级时同步）。"""
from dataclasses import dataclass, field
from enum import Enum
from uuid import UUID


class Action(Enum):
    CREATE = "create"; READ = "read"; UPDATE = "update"; DELETE = "delete"
    SHARE = "share"; MANAGE = "manage"; ACTIVATE = "activate"; DEACTIVATE = "deactivate"


class ResourceType(Enum):
    FILE = "file"; WORKSPACE = "workspace"; KNOWLEDGE = "knowledge"; APP = "app"
    USER = "user"; DOCUMENT = "document"; MODEL = "model"; CHUNK = "chunk"


@dataclass
class Resource:
    type: ResourceType; id: UUID; owner_id: UUID; tenant_id: UUID
    is_public: bool = False
    metadata: dict = field(default_factory=dict)


@dataclass
class Subject:
    id: UUID; tenant_id: UUID
    is_superuser: bool = False
    roles: set[str] = field(default_factory=set)
    workspace_memberships: set[UUID] = field(default_factory=set)


class PermissionService:
    """策略链（对齐 core service.py：Superuser → Owner → SelfAccess → Tenant）。"""

    @staticmethod
    def check(subject: Subject, action: Action, resource: Resource) -> bool:
        if subject.is_superuser:
            return True
        if subject.id == resource.owner_id:
            return True
        if resource.type == ResourceType.USER and subject.id == resource.id:
            return action in (Action.READ, Action.UPDATE)
        if subject.tenant_id == resource.tenant_id and resource.is_public:
            return action == Action.READ
        return False
