"""缓存失效共享键与 cache-aside 助手（模型域裁剪版）。

- 迁自老单体 `app/utils/redis_cache.py` 的失效/共享键部分；`@redis_cache` 装饰器
  （参数归一化 + orjson/xxhash 序列化）服务侧无调用方，未随迁
- 失效语义必须与老单体一致：`invalidate_runtime_model_info*` 与 `invalidate_workspace_model_options`
  的键形是宿主写路径与运行面共用的契约（服务侧改写键形会造成跨进程缓存不一致）
- 连接来自进程 runtime（`infrastructure.redis.configure` 装配期登记），
  不再依赖宿主模块级连接池
"""

from __future__ import annotations

import json
import logging
import random
from collections.abc import Iterable
from typing import Any

from .redis import get_async_client, get_sync_client

logger = logging.getLogger(__name__)


def _resolve_invalidation(
        key: str | None,
        prefix: str | None,
        pattern: str | None,
        batch_size: int,
) -> str | None:
    """共享的参数校验与匹配模式解析。

    ``key`` 精确删除时返回 ``None``；否则返回可用的 glob 匹配模式。

    Raises:
        ValueError: ``batch_size <= 0``，或未提供任何匹配条件。
    """
    if batch_size < 1:
        raise ValueError(f"batch_size must be >= 1, got {batch_size}")
    if key is not None:
        return None
    search = pattern or (f"cache:{prefix}:*" if prefix else None)
    if search is None:
        raise ValueError("Provide key=, prefix=, or pattern=")
    return search


async def _scan_unlink_async(redis: Any, search: str, batch_size: int) -> int:
    """异步 SCAN + UNLINK 批量删除，返回已删除的 key 数量。"""
    scan_hint = max(1, min(batch_size, 500))
    deleted = 0
    cursor = 0
    while True:
        cursor, keys = await redis.scan(cursor, match=search, count=scan_hint)
        if keys:
            for i in range(0, len(keys), batch_size):
                deleted += await redis.unlink(*keys[i:i + batch_size])
        if cursor == 0:
            break
    return deleted


def _scan_unlink_sync(redis: Any, search: str, batch_size: int) -> int:
    """同步 SCAN + UNLINK 批量删除，返回已删除的 key 数量。"""
    scan_hint = max(1, min(batch_size, 500))
    deleted = 0
    cursor = 0
    while True:
        cursor, keys = redis.scan(cursor, match=search, count=scan_hint)
        if keys:
            for i in range(0, len(keys), batch_size):
                deleted += redis.unlink(*keys[i:i + batch_size])
        if cursor == 0:
            break
    return deleted


async def invalidate_cache(
        key: str | None = None,
        *,
        prefix: str | None = None,
        pattern: str | None = None,
        batch_size: int = 1000,
) -> int:
    """主动删除缓存：精确 key，或 ``cache:{prefix}:*`` / 自定义 glob 批量删除。

    Raises:
        ValueError: 未提供任何匹配条件，或 batch_size <= 0。
    """
    search = _resolve_invalidation(key, prefix, pattern, batch_size)
    redis = await get_async_client()

    if key is not None:
        return await redis.delete(key)

    deleted = await _scan_unlink_async(redis, search, batch_size)
    if deleted:
        logger.info("Invalidated %d cache keys matching '%s'", deleted, search)
    return deleted


def invalidate_cache_sync(
        key: str | None = None,
        *,
        prefix: str | None = None,
        pattern: str | None = None,
        batch_size: int = 1000,
) -> int:
    """同步主动删除缓存，参数和 ``invalidate_cache`` 保持一致。"""
    search = _resolve_invalidation(key, prefix, pattern, batch_size)
    redis = get_sync_client()

    if key is not None:
        return redis.delete(key)

    deleted = _scan_unlink_sync(redis, search, batch_size)
    if deleted:
        logger.info("Invalidated %d cache keys matching '%s'", deleted, search)
    return deleted


def invalidate_runtime_model_info(model_id: Any) -> int:
    """同步清除指定模型配置的运行时模型信息缓存（所有租户）。

    运行时缓存 key 形如 ``runtime_model_info:{model_id}:{tenant_id or '_'}``，
    模型被禁用/删除/更新或 API Key 变更后调用，确保禁用即时生效。
    """
    return invalidate_cache_sync(pattern=f"runtime_model_info:{model_id}:*")


async def invalidate_runtime_model_info_async(model_id: Any) -> int:
    """异步清除指定模型配置的运行时模型信息缓存（所有租户）。"""
    return await invalidate_cache(pattern=f"runtime_model_info:{model_id}:*")


def invalidate_runtime_model_info_batch(
        model_ids: Iterable[Any], tenant_id: Any | None = None
) -> int:
    """批量清除若干模型配置的运行时缓存（渠道变更影响面反查后调用，单次 DEL）。

    tenant_id 已知时精确删该租户键，并附带删 `:_` 变体（防御 tenant=None 写入的旧键）；
    未知时只删 `:_`。不经 SCAN，写路径高频调用安全。
    """
    keys: list[str] = []
    seen: set[str] = set()
    for model_id in model_ids:
        candidates = (
            [f"runtime_model_info:{model_id}:{tenant_id}", f"runtime_model_info:{model_id}:_"]
            if tenant_id is not None
            else [f"runtime_model_info:{model_id}:_"]
        )
        for key in candidates:
            if key not in seen:
                seen.add(key)
                keys.append(key)
    if not keys:
        return 0
    return get_sync_client().delete(*keys)


# Explicit-key cache-aside helpers used by database read caches.
CACHE_MISS = object()
WORKSPACE_MODEL_PUBLIC_VERSION_KEY = "cache:workspace-model-options:public-version:v1"


def ttl_with_jitter(base_ttl: int) -> int:
    """Add up to 10% positive jitter to spread cache expirations."""
    return base_ttl + random.randint(0, max(1, base_ttl // 10))


def workflow_config_key(app_id: Any) -> str:
    return f"cache:workflow-config:v1:{app_id}"


def workspace_model_options_key(tenant_id: Any, public_version: str) -> str:
    return f"cache:workspace-model-options:v1:{public_version}:{tenant_id}"


def get_json(key: str) -> Any:
    try:
        raw = get_sync_client().get(key)
        if raw is None:
            return CACHE_MISS
        return json.loads(raw)
    except Exception:
        logger.warning("Redis cache read failed: key=%s", key, exc_info=True)
        return CACHE_MISS


def set_json(key: str, value: Any, ttl: int) -> None:
    try:
        payload = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
        get_sync_client().set(key, payload, ex=ttl_with_jitter(ttl))
    except Exception:
        logger.warning("Redis cache write failed: key=%s", key, exc_info=True)


def delete_json(key: str) -> None:
    try:
        get_sync_client().delete(key)
    except Exception:
        logger.warning("Redis cache invalidation failed: key=%s", key, exc_info=True)


async def get_json_async(key: str) -> Any:
    try:
        raw = await (await get_async_client()).get(key)
        if raw is None:
            return CACHE_MISS
        return json.loads(raw)
    except Exception:
        logger.warning("Redis async cache read failed: key=%s", key, exc_info=True)
        return CACHE_MISS


async def set_json_async(key: str, value: Any, ttl: int) -> None:
    try:
        payload = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
        await (await get_async_client()).set(key, payload, ex=ttl_with_jitter(ttl))
    except Exception:
        logger.warning("Redis async cache write failed: key=%s", key, exc_info=True)


async def delete_json_async(key: str) -> None:
    try:
        await (await get_async_client()).delete(key)
    except Exception:
        logger.warning("Redis async cache invalidation failed: key=%s", key, exc_info=True)


def get_workspace_model_public_version() -> str:
    try:
        value = get_sync_client().get(WORKSPACE_MODEL_PUBLIC_VERSION_KEY)
        return str(value or "0")
    except Exception:
        logger.warning("Failed to read workspace model catalog version", exc_info=True)
        return "0"


def invalidate_workspace_model_options(
        tenant_ids: Iterable[Any], *, public_catalog_changed: bool = False,
) -> None:
    try:
        redis = get_sync_client()
        if public_catalog_changed:
            redis.incr(WORKSPACE_MODEL_PUBLIC_VERSION_KEY)
            return
        version = str(redis.get(WORKSPACE_MODEL_PUBLIC_VERSION_KEY) or "0")
        keys = {
            workspace_model_options_key(tenant_id, version)
            for tenant_id in tenant_ids if tenant_id is not None
        }
        if keys:
            redis.delete(*keys)
    except Exception:
        logger.warning("Workspace model options invalidation failed", exc_info=True)
