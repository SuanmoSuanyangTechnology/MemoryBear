"""桩转发与 catch-all 转发：/stub、/v1/stub 回显身份上下文（e2e 断言剥凭据/身份头），
其余未注册路径经中间件解析 target_route 后由 Forwarder 真实转发到目标服务。

/stub 为用户 JWT 桩（/api/ 语义），/v1/stub 为 API key 桩（/v1/ 外部集成流量）——
两条路径由中间件按 API_KEY_PATH_PREFIXES 判定，此处仅回显各自注入的身份头。
"""
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from auth_sdk.schema import UserContext, ApiKeyContext
from src.forward import Forwarder

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/healthz")
async def healthz():
    return {"status": "ok"}


@router.get("/stub")
async def stub(request: Request):
    identity = request.state.identity
    if not isinstance(identity, UserContext):
        # 防御：/stub 不是 API key 路径，走到这里只可能是用户 JWT 身份
        return JSONResponse(status_code=400, content={"error": "expects user identity"})
    return {
        "user_id": identity.user_id, "tenant_id": identity.tenant_id,
        "workspace_id": identity.workspace_id,
        "internal_token": request.state.internal_token,
        "has_user_authorization": "authorization" in request.headers,
        # 中间件注入后的下游请求头回显（供 e2e/单测断言身份头注入）
        "x_headers": {
            "x-user-id": request.headers.get("x-user-id"),
            "x-tenant-id": request.headers.get("x-tenant-id"),
            "x-workspace-id": request.headers.get("x-workspace-id"),
        },
    }


@router.get("/v1/stub")
async def api_key_stub(request: Request):
    identity = request.state.identity
    if not isinstance(identity, ApiKeyContext):
        # 防御：/v1/ 路径下身份只可能是 API key
        return JSONResponse(status_code=400, content={"error": "expects api key identity"})
    return {
        "api_key_id": identity.api_key_id,
        "tenant_id": identity.tenant_id,
        "workspace_id": identity.workspace_id,
        "scopes": identity.scopes,
        "internal_token": request.state.internal_token,
        "has_x_api_key": "x-api-key" in request.headers,   # 剥除后应为 False
        "x_headers": {
            "x-api-key-id": request.headers.get("x-api-key-id"),
            "x-tenant-id": request.headers.get("x-tenant-id"),
            "x-workspace-id": request.headers.get("x-workspace-id"),
        },
    }


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD"])
async def forward_route(request: Request):
    route = getattr(request.state, "target_route", None)
    if route is None:
        logger.warning("unmatched route: %s %s", request.method, request.url.path)
        return JSONResponse(status_code=404, content={"detail": "not found"})
    forwarder: Forwarder = request.app.state.forwarder
    return await forwarder.forward(request, route)
