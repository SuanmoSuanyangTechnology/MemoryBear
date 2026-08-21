"""Request-scoped dependencies for internal Knowledge interfaces."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Header, Request
from pydantic import BaseModel, ValidationError

from ..errors import KnowledgeError
from ..runtime import ProcessRuntime


class Principal(BaseModel):
    """Authenticated caller identity forwarded by the API service."""

    actor_id: UUID
    actor_name: str | None = None
    tenant_id: UUID
    workspace_id: UUID


def get_runtime(request: Request) -> ProcessRuntime:
    """Return the process runtime owned by the current application."""

    return request.app.state.runtime


async def get_principal(
    actor_id: Annotated[str | None, Header(alias="X-KB-Actor-ID")] = None,
    tenant_id: Annotated[str | None, Header(alias="X-KB-Tenant-ID")] = None,
    workspace_id: Annotated[str | None, Header(alias="X-KB-Workspace-ID")] = None,
    actor_name: Annotated[str | None, Header(alias="X-KB-Actor-Name")] = None,
) -> Principal:
    """Parse the trusted internal identity headers as one ordinary DTO."""

    try:
        return Principal.model_validate(
            {
                "actor_id": actor_id,
                "actor_name": actor_name,
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
            }
        )
    except ValidationError as exc:
        raise KnowledgeError.from_code(
            "KB_PRINCIPAL_INVALID",
            "Invalid knowledge principal headers",
        ) from exc


__all__ = ["Principal", "get_principal", "get_runtime"]
