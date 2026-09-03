"""API key 快照组装（只读映射 api_keys 表（identity.models），哈希 = sha256(明文)，5.3）。"""
import json
from datetime import datetime

from auth_sdk.snapshot import api_key_hash

from src.models.base import utcnow_naive
from src.repositories.api_key import get_api_key
from src.repositories.user import get_workspace


async def build_api_key_snapshot(session, api_key: str) -> dict | None:
    row = await get_api_key(session, api_key)
    if row is None or not row.is_active:
        return None
    # core ApiKey.expires_at 为 naive UTC（utcnow_naive 语义）；过期 key 不产出快照
    if row.expires_at is not None and row.expires_at < utcnow_naive():
        return None
    workspace = await get_workspace(session, row.workspace_id)
    tenant_id = str(workspace.tenant_id) if workspace else ""
    return {
        "api_key_id": str(row.id), "workspace_id": str(row.workspace_id),
        "tenant_id": tenant_id, "scopes": list(row.scopes or []),
        "rate_limit": row.rate_limit,
        "daily_request_limit": row.daily_request_limit,
        "rate_limit_disabled": bool(row.rate_limit_disabled),
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,  # 仅写入用，不落 Redis
    }


async def write_api_key_snapshot(redis, api_key: str, snapshot: dict) -> None:
    """快照生命周期对齐 key 真实有效期：expires_at 有值 → TTL = 剩余秒（恰好在 key 过期时
    消失，网关 miss → 401）；None（永不过期）→ 不设过期（吊销靠 notify 删 + reconcile 1min 兜底）。

    与用户快照（24h + GETEX 续期）不同：API key 是长期静态凭证，无"访问续期"动作，
    网关读取时也不续期（auth_sdk.get_api_key_snapshot 用 GET）——续期会把仍在有效期的
    key 在 24h 无访问后 miss，或把永不过期 key 续成 24h 错误缩短。
    """
    expires_at = snapshot.pop("expires_at", None)
    payload = json.dumps(snapshot)
    key = f"api_key:{api_key_hash(api_key)}"
    if expires_at is None:
        await redis.set(key, payload)
    else:
        ttl = max(1, int((datetime.fromisoformat(expires_at) - utcnow_naive()).total_seconds()))
        await redis.set(key, payload, ex=ttl)
