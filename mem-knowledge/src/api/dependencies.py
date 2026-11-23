"""Request-scoped dependencies for internal Knowledge interfaces."""

from __future__ import annotations

from uuid import UUID

from fastapi import Request
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
    """Return the middleware principal or trusted identity-header fallback."""

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


async def require_knowledge_copy_principal(request: Request) -> Principal:
    """配置复制信任网关断言的管理端面。

    两种鉴权形态都满足：direct 头信任与企业 claims 各自设置已认证主体，上游注入
    管理端面。面标识检查排除外部/API-Key 调用方；凭据已在上游终止，其存在不再是
    信任信号（企业网关会把 internal token 作为 Authorization 转发）。网络边界仍
    限定 kb 只接受网关 + 单体调用方。
    """

    if not is_manager_request(request):
        raise KnowledgeError.from_code("KB_HTTP_FORBIDDEN")
    return await get_principal(request)


def _resolve_source_value(request: Request) -> str | None:
    """从两种传输方言解析调用方断言的来源。

    网关终止凭据后以通用 ``x-source`` 头注入面标识；单体代理与东西向内部调用方
    送 kb 专属 ``X-KB-Source``（还携带 workflow/agent 等业务来源）。优先网关面
    标识，回落到 kb 头。
    """

    return request.headers.get("X-Source") or request.headers.get("X-KB-Source")


async def get_source(request: Request) -> KnowledgeRetrievalSource:
    """解析调用方断言的业务来源（网关面标识或 kb 头）。"""

    value = _resolve_source_value(request)
    try:
        return KnowledgeRetrievalSource(value or KnowledgeRetrievalSource.GENERAL)
    except ValueError as exc:
        raise KnowledgeError.from_code(
            "KB_SOURCE_HEADER_INVALID",
        ) from exc


def is_manager_request(request: Request) -> bool:
    return _resolve_source_value(request) == KnowledgeRetrievalSource.MANAGER_API


__all__ = [
    "Principal",
    "get_optional_principal",
    "get_principal",
    "get_runtime",
    "get_source",
    "is_manager_request",
    "require_knowledge_copy_principal",
]
