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


def _stub_identity(request: Request, expected: type) -> object | None:
    """direct 模式（默认 AUTH_STRATEGY=direct）不终结凭据——中间件不解析身份、
    不注入身份头，request.state 无 identity/internal_token；stub 回显仅对 gateway
    终结策略有定义。身份缺失返回 None，由调用方给明确 400（而非 500 AttributeError）。
    """
    identity = getattr(request.state, "identity", None)
    if not isinstance(identity, expected):
        return None
    return identity


@router.get("/stub")
async def stub(request: Request):
    identity = _stub_identity(request, UserContext)
    if identity is None:
        # direct 模式无身份可回显；非 API key 身份（防御）同样拒绝
        return JSONResponse(status_code=400, content={
            "error": "expects user identity",
            "detail": "identity not resolved: /stub requires AUTH_STRATEGY=gateway "
                      "(direct mode passes credentials through for downstream verification)"})
    return {
        "user_id": identity.user_id, "tenant_id": identity.tenant_id,
        "workspace_id": identity.workspace_id,
        "internal_token": getattr(request.state, "internal_token", None),
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
    identity = _stub_identity(request, ApiKeyContext)
    if identity is None:
        return JSONResponse(status_code=400, content={
            "error": "expects api key identity",
            "detail": "identity not resolved: /v1/stub requires AUTH_STRATEGY=gateway "
                      "(direct mode passes credentials through for downstream verification)"})
    return {
        "api_key_id": identity.api_key_id,
        "tenant_id": identity.tenant_id,
        "workspace_id": identity.workspace_id,
        "scopes": identity.scopes,
        "internal_token": getattr(request.state, "internal_token", None),
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
