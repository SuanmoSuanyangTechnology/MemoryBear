"""Request-scoped dependencies for the internal model service API."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated
from urllib.parse import unquote
from uuid import UUID

from fastapi import Header, HTTPException, Request
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from ..runtime import ProcessRuntime

ACTOR_ID_HEADER = "X-Model-Actor-ID"
ACTOR_NAME_HEADER = "X-Model-Actor-Name"
TENANT_ID_HEADER = "X-Model-Tenant-ID"
WORKSPACE_ID_HEADER = "X-Model-Workspace-ID"
SOURCE_HEADER = "X-Model-Source"


class Principal(BaseModel):
    """Authenticated caller identity forwarded by the host API service."""

    actor_id: UUID
    actor_name: str | None = None
    tenant_id: UUID
    workspace_id: UUID | None = None
    source: str | None = None


def get_runtime(request: Request) -> ProcessRuntime:
    """Return the process runtime owned by the current application."""

    return request.app.state.runtime


def get_sync_db(request: Request) -> Iterator[Session]:
    """请求级同步会话（管理面 sync：阶段二决策见设计 §2.3，M8–M10 统一 sync→async）。

    会话生命周期与请求对齐：未提交事务在退出时回滚，与宿主 ``get_db`` 同口径。
    """

    runtime: ProcessRuntime = request.app.state.runtime
    with runtime.database.sync_session() as session:
        yield session


def _decode_actor_name(value: str | None) -> str | None:
    """还原宿主对 actor name 的 UTF-8 百分号编码（HTTP 头值仅允许 ASCII）。"""

    if value is None:
        return None
    return unquote(value)


def principal_from_headers(request: Request) -> Principal:
    """通道 2 解析：受信内部调用方按身份头构造主体。"""

    try:
        return Principal.model_validate(
            {
                "actor_id": request.headers.get(ACTOR_ID_HEADER),
                "actor_name": _decode_actor_name(request.headers.get(ACTOR_NAME_HEADER)),
                "tenant_id": request.headers.get(TENANT_ID_HEADER),
                "workspace_id": request.headers.get(WORKSPACE_ID_HEADER),
                "source": request.headers.get(SOURCE_HEADER),
            }
        )
    except ValidationError as exc:
        raise HTTPException(status_code=401, detail="invalid principal headers") from exc


async def get_principal(request: Request) -> Principal:
    """身份依赖：中间件解析结果优先，缺失时按内部头兜底解析。"""

    principal = getattr(request.state, "principal", None)
    if isinstance(principal, Principal):
        return principal
    return principal_from_headers(request)


async def get_optional_principal(request: Request) -> Principal | None:
    principal = getattr(request.state, "principal", None)
    if isinstance(principal, Principal):
        return principal
    if all(
        request.headers.get(header) is None
        for header in (ACTOR_ID_HEADER, TENANT_ID_HEADER, WORKSPACE_ID_HEADER)
    ):
        return None
    return principal_from_headers(request)


async def get_source(
    value: Annotated[str | None, Header(alias=SOURCE_HEADER)] = None,
) -> str | None:
    """Parse the API-asserted business source."""

    return value


__all__ = [
    "ACTOR_ID_HEADER",
    "ACTOR_NAME_HEADER",
    "SOURCE_HEADER",
    "TENANT_ID_HEADER",
    "WORKSPACE_ID_HEADER",
    "Principal",
    "get_optional_principal",
    "get_principal",
    "get_runtime",
    "get_source",
    "get_sync_db",
    "principal_from_headers",
]
