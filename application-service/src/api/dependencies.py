"""Request-scoped dependencies for the internal application service API."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from uuid import UUID

from fastapi import HTTPException, Request
from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ..runtime import ProcessRuntime

ACTOR_ID_HEADER = "X-App-Actor-ID"
TENANT_ID_HEADER = "X-App-Tenant-ID"
WORKSPACE_ID_HEADER = "X-App-Workspace-ID"


class Principal(BaseModel):
    """Internal-call principal: the actor is always required (writes land with a
    created_by owner), tenant is required, workspace is optional."""

    actor_id: UUID
    tenant_id: UUID
    workspace_id: UUID | None = None


def get_runtime(request: Request) -> ProcessRuntime:
    """Return the process runtime owned by the current application."""

    return request.app.state.runtime


def get_sync_db(request: Request) -> Iterator[Session]:
    """Request-scoped sync session (management plane is synchronous).

    The session lifetime matches the request: an uncommitted transaction is
    rolled back on exit, the same contract as the host ``get_db``.
    """

    runtime: ProcessRuntime = request.app.state.runtime
    with runtime.database.sync_session() as session:
        yield session


async def get_async_db(request: Request) -> AsyncIterator[AsyncSession]:
    """Request-scoped async session (runtime plane: resolve paths, streaming).

    Same lifetime contract as ``get_sync_db`` (uncommitted transactions roll
    back on exit) but on the asyncpg engine. Runtime-plane routes must always
    inject this dependency: a sync session inside the event loop blocks it, and
    the two must never be mixed.
    """

    runtime: ProcessRuntime = request.app.state.runtime
    async with runtime.database.async_session() as session:
        yield session


def _principal_payload(request: Request) -> dict[str, str | None]:
    return {
        "actor_id": request.headers.get(ACTOR_ID_HEADER),
        "tenant_id": request.headers.get(TENANT_ID_HEADER),
        "workspace_id": request.headers.get(WORKSPACE_ID_HEADER),
    }


def principal_from_headers(request: Request) -> Principal:
    """Direct-mode parse: build the principal from the injected identity headers
    (actor is required, fail-closed)."""

    try:
        return Principal.model_validate(_principal_payload(request))
    except ValidationError as exc:
        raise HTTPException(status_code=401, detail="invalid principal headers") from exc


async def get_principal(request: Request) -> Principal:
    """Identity dependency: the middleware parse result wins; without it, fall
    back to parsing the internal headers."""

    principal = getattr(request.state, "principal", None)
    if isinstance(principal, Principal):
        return principal
    return principal_from_headers(request)


__all__ = [
    "ACTOR_ID_HEADER",
    "TENANT_ID_HEADER",
    "WORKSPACE_ID_HEADER",
    "Principal",
    "get_async_db",
    "get_principal",
    "get_runtime",
    "get_sync_db",
    "principal_from_headers",
]
