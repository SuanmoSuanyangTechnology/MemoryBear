"""API key 回源接口：网关快照 miss 时用明文 key 换取快照（identity 是凭据权威）。

与 /internal/user-snapshot、/internal/acl-rules 同规格：不校验调用方凭据，
来源约束由 NetworkPolicy 承担（集群内可控）。

响应 200 = 可写回 Redis 的快照 payload（含 expires_at，网关据此设置 TTL）；
401 = key 不存在/停用/已过期。DB 故障向上抛 500，网关按不可用 fail-closed。
"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_async_db
from src.services.api_keys import build_api_key_snapshot

router = APIRouter()


def _bearer_token(request: Request) -> str | None:
    value = request.headers.get("authorization", "")
    scheme, _, token = value.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return token.strip() or None


@router.post("/internal/api-key-verify")
async def api_key_verify(
    request: Request, session: AsyncSession = Depends(get_async_db)
):
    api_key = _bearer_token(request)
    if api_key is None:
        return JSONResponse(status_code=401, content={"error": "missing api key"})
    snapshot = await build_api_key_snapshot(session, api_key)
    if snapshot is None:
        return JSONResponse(status_code=401, content={"error": "invalid api key"})
    return snapshot
