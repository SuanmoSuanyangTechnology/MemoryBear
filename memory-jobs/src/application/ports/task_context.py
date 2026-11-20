"""Validated task identity and scoped context repository contract."""

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class TaskScope:
    workspace_id: UUID
    tenant_id: UUID
    end_user_id: UUID
    actor_id: UUID | None
    config_id: str | None = None

    def payload(self) -> dict[str, Any]:
        return {
            "workspace_id": str(self.workspace_id),
            "tenant_id": str(self.tenant_id),
            "end_user_id": str(self.end_user_id),
            "actor_id": str(self.actor_id) if self.actor_id else None,
            "config_id": self.config_id,
        }


class TaskContextRepository(Protocol):
    def context(self, scope: TaskScope) -> dict[str, Any]: ...
    def check(self, context: dict[str, Any], *, require_config: bool = True) -> None: ...
