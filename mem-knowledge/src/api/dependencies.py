"""Request-scoped dependencies for internal Knowledge interfaces."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Header, Request
from pydantic import BaseModel, ValidationError

from ..errors import KnowledgeError
from ..runtime import ProcessRuntime
from .schemas.chunk import KnowledgeRetrievalSource


class Principal(BaseModel):
    """Authenticated caller identity forwarded by the API service."""

    actor_id: UUID
    actor_name: str | None = None
    tenant_id: UUID
    workspace_id: UUID


def get_runtime(request: Request) -> ProcessRuntime:
    """Return the process runtime owned by the current application."""

    return request.app.state.runtime


def _principal_from_headers(request: Request) -> Principal:
    """通道 2 兜底解析：老单体直连（已由中间件放行）按身份头解析。"""

    try:
        return Principal.model_validate(
            {
                "actor_id": request.headers.get("X-KB-Actor-ID"),
                "actor_name": request.headers.get("X-KB-Actor-Name"),
                "tenant_id": request.headers.get("X-KB-Tenant-ID"),
                "workspace_id": request.headers.get("X-KB-Workspace-ID"),
            }
        )
    except ValidationError as exc:
        raise KnowledgeError.from_code(
            "KB_PRINCIPAL_INVALID",
            "Invalid knowledge principal headers",
        ) from exc


async def get_principal(request: Request) -> Principal:
    """身份依赖：中间件验签结果优先（通道 1 claims 权威），否则按原头解析（通道 2）。

    路由签名不变：Depends(get_principal) 由 FastAPI 自动注入 Request。
    """

    principal = getattr(request.state, "principal", None)
    if isinstance(principal, Principal):
        return principal
    return _principal_from_headers(request)


async def get_optional_principal(request: Request) -> Principal | None:
    """Return no principal only when every trusted identity header is absent."""

    principal = getattr(request.state, "principal", None)
    if isinstance(principal, Principal):
        return principal
    values = (
        request.headers.get("X-KB-Actor-ID"),
        request.headers.get("X-KB-Tenant-ID"),
        request.headers.get("X-KB-Workspace-ID"),
        request.headers.get("X-KB-Actor-Name"),
    )
    if all(value is None for value in values):
        return None
    return _principal_from_headers(request)


async def get_source(
    value: Annotated[str | None, Header(alias="X-KB-Source")] = None,
) -> KnowledgeRetrievalSource:
    """Parse the API-asserted business source."""

    try:
        return KnowledgeRetrievalSource(value or KnowledgeRetrievalSource.GENERAL)
    except ValueError as exc:
        raise KnowledgeError.from_code(
            "KB_PRINCIPAL_INVALID",
            "Invalid knowledge source header",
        ) from exc


__all__ = [
    "Principal",
    "get_optional_principal",
    "get_principal",
    "get_runtime",
    "get_source",
]
