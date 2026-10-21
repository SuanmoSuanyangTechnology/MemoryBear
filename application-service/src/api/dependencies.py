"""Request-scoped dependencies for the internal application service API."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel, ValidationError
from redis import Redis as SyncRedis
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ..infrastructure.upstream import MonolithUpstream
from ..integrations.model_invoke import ModelInvokeClient
from ..runtime import ProcessRuntime

ACTOR_ID_HEADER = "X-App-Actor-ID"
TENANT_ID_HEADER = "X-App-Tenant-ID"
WORKSPACE_ID_HEADER = "X-App-Workspace-ID"
# API-key identity header injected by the gateway on ``/v1`` (API-key) paths:
# the key row id, from which the service resolves the key's resource binding.
API_KEY_ID_HEADER = "X-Api-Key-ID"


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


def get_upstream(request: Request) -> MonolithUpstream:
    """Process-wide monolith upstream client (cross-domain write forwarding)."""

    runtime: ProcessRuntime = request.app.state.runtime
    return runtime.upstream


def get_model_invoke(request: Request) -> ModelInvokeClient:
    """Process-wide model-service invoke client (embedding generation)."""

    runtime: ProcessRuntime = request.app.state.runtime
    return runtime.model_invoke


def get_sync_redis(request: Request) -> SyncRedis:
    """Process-wide sync Redis client for the shared-key cache contract.

    ``from_url`` does not connect eagerly, and the cache helpers swallow
    connection errors, so an unreachable Redis degrades to a stale cache
    instead of failing requests — the same posture as the monolith helper.
    """

    runtime: ProcessRuntime = request.app.state.runtime
    return runtime.redis.sync_client()


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


# Bearer extraction for public share-access tokens (host parity:
# ``OAuth2PasswordBearer`` answers a missing header with 401 + WWW-Authenticate).
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")


class ShareTokenData:
    """Decoded share-access token: the viewer id bound to one share token."""

    def __init__(self, user_id: str, share_token: str):
        self.user_id = user_id
        self.share_token = share_token


def get_share_user_id(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: Annotated[Session, Depends(get_sync_db)],
) -> ShareTokenData:
    """Validate the share-access token of a public share request.

    Sync-style on purpose: the body makes sync DB calls, so FastAPI runs it in
    the threadpool instead of blocking the event loop. Validity only requires
    the share to exist and be enabled (a password share still resolves here);
    any failure surfaces as 401.
    """

    from ..errors import BusinessException
    from ..services.release_share_service import ReleaseShareService
    from ..utils.share_token import decode_access_token

    credentials_exception = HTTPException(
        status_code=401,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = decode_access_token(token)
        user_id = payload["user_id"]
        share_token = payload["share_token"]

        share_info = ReleaseShareService(db).get_shared_release_info(
            share_token=share_token
        )
    except BusinessException as e:
        raise credentials_exception from e
    except Exception as e:
        raise credentials_exception from e

    if not share_info:
        raise credentials_exception

    return ShareTokenData(user_id=user_id, share_token=share_token)


__all__ = [
    "ACTOR_ID_HEADER",
    "API_KEY_ID_HEADER",
    "TENANT_ID_HEADER",
    "WORKSPACE_ID_HEADER",
    "ModelInvokeClient",
    "MonolithUpstream",
    "Principal",
    "ShareTokenData",
    "SyncRedis",
    "get_async_db",
    "get_model_invoke",
    "get_principal",
    "get_runtime",
    "get_share_user_id",
    "get_sync_db",
    "get_sync_redis",
    "get_upstream",
    "principal_from_headers",
]
