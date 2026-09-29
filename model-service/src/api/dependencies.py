"""Request-scoped dependencies for the internal model service API."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Annotated
from urllib.parse import unquote
from uuid import UUID

from fastapi import Header, HTTPException, Request
from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ..runtime import ProcessRuntime

ACTOR_ID_HEADER = "X-Model-Actor-ID"
ACTOR_NAME_HEADER = "X-Model-Actor-Name"
TENANT_ID_HEADER = "X-Model-Tenant-ID"
WORKSPACE_ID_HEADER = "X-Model-Workspace-ID"
SOURCE_HEADER = "X-Model-Source"


class InvokePrincipal(BaseModel):
    """运行面主体（设计 §2.2）：actor 可缺省——Celery 等调用方没有用户身份。"""

    actor_id: UUID | None = None
    actor_name: str | None = None
    tenant_id: UUID
    workspace_id: UUID | None = None
    source: str | None = None


class Principal(InvokePrincipal):
    """管理面主体：在运行面身份上收紧——落库（created_by）必须知道是谁在操作。"""

    actor_id: UUID


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


async def get_async_db(request: Request) -> AsyncIterator[AsyncSession]:
    """请求级异步会话（运行面 invoke：解析/解密/校验读路径）。

    与 ``get_sync_db`` 同生命周期口径（退出未提交即回滚），但走 asyncpg 引擎。
    运行面路由一律注入本依赖：sync 会话在事件循环内会阻塞，二者不可混用。
    """

    runtime: ProcessRuntime = request.app.state.runtime
    async with runtime.database.async_session() as session:
        yield session


def _decode_actor_name(value: str | None) -> str | None:
    """还原宿主对 actor name 的 UTF-8 百分号编码（HTTP 头值仅允许 ASCII）。"""

    if value is None:
        return None
    return unquote(value)


def _principal_payload(request: Request) -> dict[str, str | None]:
    return {
        "actor_id": request.headers.get(ACTOR_ID_HEADER),
        "actor_name": _decode_actor_name(request.headers.get(ACTOR_NAME_HEADER)),
        "tenant_id": request.headers.get(TENANT_ID_HEADER),
        "workspace_id": request.headers.get(WORKSPACE_ID_HEADER),
        "source": request.headers.get(SOURCE_HEADER),
    }


def principal_from_headers(request: Request) -> Principal:
    """通道 2 解析（管理面）：受信内部调用方按身份头构造主体，actor 必填。"""

    try:
        return Principal.model_validate(_principal_payload(request))
    except ValidationError as exc:
        raise HTTPException(status_code=401, detail="invalid principal headers") from exc


def invoke_principal_from_headers(request: Request) -> InvokePrincipal:
    """通道 2 解析（运行面）：actor 可缺省，tenant 仍必填（fail-closed）。"""

    try:
        return InvokePrincipal.model_validate(_principal_payload(request))
    except ValidationError as exc:
        raise HTTPException(status_code=401, detail="invalid principal headers") from exc


async def get_principal(request: Request) -> Principal:
    """管理面身份依赖：中间件解析结果优先，缺失时按内部头兜底解析。"""

    principal = getattr(request.state, "principal", None)
    if isinstance(principal, Principal):
        return principal
    return principal_from_headers(request)


async def get_invoke_principal(request: Request) -> InvokePrincipal:
    """运行面身份依赖：中间件结果优先（管理面严格主体是其子类，同样兼容）。"""

    principal = getattr(request.state, "principal", None)
    if isinstance(principal, InvokePrincipal):
        return principal
    return invoke_principal_from_headers(request)


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
    "InvokePrincipal",
    "Principal",
    "get_async_db",
    "get_invoke_principal",
    "get_optional_principal",
    "get_principal",
    "get_runtime",
    "get_source",
    "get_sync_db",
    "invoke_principal_from_headers",
    "principal_from_headers",
]
