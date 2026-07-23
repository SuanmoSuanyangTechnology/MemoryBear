"""
Redis 查询结果缓存装饰器

提供 ``@redis_cache`` 装饰器，自动缓存异步函数的返回值到 Redis

用法::

    from app.utils.redis_cache import redis_cache

    # skip_args 支持参数名，db 等连接参数自动跳过
    @redis_cache(ttl=300, prefix="forget_logs", skip_args=["db"])
    async def get_forget_logs(db, end_user_id, page=1, pagesize=10):
        ...

    # 也支持位置索引
    @redis_cache(ttl=120, prefix="user", skip_args=[0])
    async def get_user(db, user_id):
        ...

    # 自定义 key 构建
    @redis_cache(ttl=60, key_builder=lambda *a, **kw: f"custom:{kw['user_id']}")
    async def expensive_query(user_id):
        ...

Key 格式： ``cache:{prefix}:{qualname}:{args_hash}``
"""

import hashlib
import inspect
import json
import logging
import random
import uuid
from enum import Enum
from functools import wraps
from typing import Any, Callable, Iterable

from app.aioRedis import get_thread_safe_redis, get_thread_safe_sync_redis

logger = logging.getLogger(__name__)

DEFAULT_TTL = 300  # 默认 5 分钟


def _resolve_skip_indices(
        func: Callable,
        skip_args: list[int | str],
) -> frozenset[int]:
    """将 ``skip_args`` 中的参数名解析为位置索引，与已有的 int 索引合并。"""
    indices: set[int] = set()
    names: set[str] = set()
    for v in skip_args:
        if isinstance(v, int):
            indices.add(v)
        else:
            names.add(v)

    if names:
        try:
            sig = inspect.signature(func)
            for i, (pname, _param) in enumerate(sig.parameters.items()):
                if pname in names:
                    indices.add(i)
        except (ValueError, TypeError):
            pass

    return frozenset(indices)


def _default_key_builder(
        prefix: str,
        func: Callable,
        args: tuple,
        kwargs: dict,
        skip_indices: frozenset[int],
) -> str:
    """默认缓存 key 构建器。

    Key 格式：``cache:{prefix}:{qualname}:{args_hash}``
    """
    qualname = getattr(func, "__qualname__", func.__name__)
    # 跳过标记位置的参数
    if skip_indices:
        filtered_args = tuple(
            v for i, v in enumerate(args) if i not in skip_indices
        )
    else:
        filtered_args = args

    raw = _make_hashable(filtered_args, kwargs)
    payload = json.dumps(raw, sort_keys=True, ensure_ascii=False, default=str)
    digest = hashlib.md5(payload.encode()).hexdigest()[:12]
    return f"cache:{prefix}:{qualname}:{digest}"


def _make_hashable(args: tuple, kwargs: dict) -> list:
    """将 args/kwargs 转为 JSON 可序列化的列表，用于 hash 计算。"""
    result: list[Any] = []
    for v in args:
        result.append(_to_hashable(v))
    if kwargs:
        result.append(dict(sorted((k, _to_hashable(v)) for k, v in kwargs.items())))
    return result


def _to_hashable(v: Any) -> Any:
    """将单个值转为 hash-key"""
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, Enum):
        return v.value
    if hasattr(v, "model_dump"):
        try:
            return v.model_dump(mode="json")
        except Exception:
            return str(v)
    if isinstance(v, (bytes, bytearray)):
        return v.hex()
    if isinstance(v, (set, frozenset)):
        return sorted(_to_hashable(x) for x in v)
    if isinstance(v, dict):
        return {str(k): _to_hashable(val) for k, val in v.items()}
    if isinstance(v, (list, tuple)):
        return [_to_hashable(x) for x in v]
    return v


def redis_cache(
        ttl: int = DEFAULT_TTL,
        prefix: str = "default",
        skip_args: list[int | str] | None = None,
        key_builder: Callable[..., str] | None = None,
        cache_none: bool = False,
) -> Callable:
    """函数返回值 Redis 缓存装饰器，同时支持同步和异步函数。

    Args:
        ttl: 缓存过期时间（秒），默认 300。
        prefix: 缓存 key 前缀，最终 key 为 ``cache:{prefix}:...``。
        skip_args: 不参与 key 计算的参数。支持位置索引 (int) 或参数名 (str)。
                   例：``skip_args=[0]`` 跳过第一个位置参数；
                   ``skip_args=["db", "redis"]`` 跳过名为 db、redis 的参数。
        key_builder: 自定义 key 构建函数，签名为
                     ``(prefix, func, args, kwargs) -> str``。
                     提供后忽略 ``skip_args`` 和默认 key 逻辑。
        cache_none: 是否缓存 ``None`` 返回值。默认 ``False``，避免缓存空结果。
    """

    def deco(func: Callable):
        _skip_indices = _resolve_skip_indices(func, skip_args or [])

        def _build_key(fn: Callable, args: tuple, kwargs: dict) -> str:
            if key_builder is not None:
                return key_builder(prefix, fn, args, kwargs)
            return _default_key_builder(prefix, fn, args, kwargs, _skip_indices)

        async def _cache_read_write(cache_key: str, compute):
            """共享的缓存读取→回退计算→写入逻辑（异步）。"""
            redis = get_thread_safe_redis()

            # 尝试读取缓存
            try:
                cached = await redis.get(cache_key)
            except Exception:
                logger.warning("Redis GET failed for key=%s", cache_key, exc_info=True)
                cached = None

            if cached is not None:
                try:
                    result = json.loads(cached)
                    logger.debug("Cache HIT: %s", cache_key)
                    return result
                except json.JSONDecodeError:
                    logger.warning(
                        "Corrupted cache data for key=%s, will recompute", cache_key,
                    )

            # 缓存未命中，执行原函数
            result = await compute()

            # 决定是否写入缓存
            if result is None and not cache_none:
                return None

            try:
                value = json.dumps(result, ensure_ascii=False, default=str)
                await redis.set(cache_key, value, ex=ttl)
                logger.debug("Cache SET: %s (ttl=%ds)", cache_key, ttl)
            except Exception:
                logger.warning("Redis SET failed for key=%s", cache_key, exc_info=True)

            return result

        if inspect.iscoroutinefunction(func):
            # async
            @wraps(func)
            async def async_wrapper(*args, **kwargs):
                cache_key = _build_key(func, args, kwargs)

                async def _compute():
                    return await func(*args, **kwargs)

                return await _cache_read_write(cache_key, _compute)

            async_wrapper._cache_prefix = prefix
            async_wrapper._cache_ttl = ttl
            return async_wrapper

        else:
            # sync
            @wraps(func)
            def sync_wrapper(*args, **kwargs):
                cache_key = _build_key(func, args, kwargs)
                redis = get_thread_safe_sync_redis()

                # 读取缓存
                try:
                    cached = redis.get(cache_key)
                except Exception:
                    logger.warning("Redis GET failed for key=%s", cache_key, exc_info=True)
                    cached = None

                if cached is not None:
                    try:
                        result = json.loads(cached)
                        logger.debug("Cache HIT: %s", cache_key)
                        return result
                    except json.JSONDecodeError:
                        logger.warning(
                            "Corrupted cache data for key=%s, will recompute", cache_key,
                        )

                # 缓存未命中
                result = func(*args, **kwargs)

                if result is None and not cache_none:
                    return None

                try:
                    value = json.dumps(result, ensure_ascii=False, default=str)
                    redis.set(cache_key, value, ex=ttl)
                    logger.debug("Cache SET: %s (ttl=%ds)", cache_key, ttl)
                except Exception:
                    logger.warning("Redis SET failed for key=%s", cache_key, exc_info=True)

                return result

            sync_wrapper._cache_prefix = prefix
            sync_wrapper._cache_ttl = ttl
            return sync_wrapper

    return deco


async def invalidate_cache(
        key: str | None = None,
        *,
        prefix: str | None = None,
        pattern: str | None = None,
) -> int:
    """主动删除缓存。

    三种调用方式::

        await invalidate_cache(key="cache:user:get_user:abc123")
        await invalidate_cache(prefix="user")           # 删除 cache:user:* 开头的所有 key
        await invalidate_cache(pattern="cache:user:*")  # 自定义 glob 模式

    Returns:
        已删除的 key 数量。

    Raises:
        ValueError: 未提供任何匹配条件。
    """
    redis = get_thread_safe_redis()

    if key is not None:
        return await redis.delete(key)

    search = pattern or (f"cache:{prefix}:*" if prefix else None)
    if search is None:
        raise ValueError("Provide key=, prefix=, or pattern=")

    deleted = 0
    cursor = 0
    while True:
        cursor, keys = await redis.scan(cursor, match=search, count=500)
        if keys:
            deleted += await redis.unlink(*keys)
        if cursor == 0:
            break

    logger.info("Invalidated %d cache keys matching '%s'", deleted, search)
    return deleted


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
        raw = get_thread_safe_sync_redis().get(key)
        if raw is None:
            return CACHE_MISS
        return json.loads(raw)
    except Exception:
        logger.warning("Redis cache read failed: key=%s", key, exc_info=True)
        return CACHE_MISS


def set_json(key: str, value: Any, ttl: int) -> None:
    try:
        payload = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
        get_thread_safe_sync_redis().set(key, payload, ex=ttl_with_jitter(ttl))
    except Exception:
        logger.warning("Redis cache write failed: key=%s", key, exc_info=True)


def delete_json(key: str) -> None:
    try:
        get_thread_safe_sync_redis().delete(key)
    except Exception:
        logger.warning("Redis cache invalidation failed: key=%s", key, exc_info=True)


async def get_json_async(key: str) -> Any:
    try:
        raw = await get_thread_safe_redis().get(key)
        if raw is None:
            return CACHE_MISS
        return json.loads(raw)
    except Exception:
        logger.warning("Redis async cache read failed: key=%s", key, exc_info=True)
        return CACHE_MISS


async def set_json_async(key: str, value: Any, ttl: int) -> None:
    try:
        payload = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
        await get_thread_safe_redis().set(key, payload, ex=ttl_with_jitter(ttl))
    except Exception:
        logger.warning("Redis async cache write failed: key=%s", key, exc_info=True)


async def delete_json_async(key: str) -> None:
    try:
        await get_thread_safe_redis().delete(key)
    except Exception:
        logger.warning("Redis async cache invalidation failed: key=%s", key, exc_info=True)


def get_workspace_model_public_version() -> str:
    try:
        value = get_thread_safe_sync_redis().get(WORKSPACE_MODEL_PUBLIC_VERSION_KEY)
        return str(value or "0")
    except Exception:
        logger.warning("Failed to read workspace model catalog version", exc_info=True)
        return "0"


def invalidate_workspace_model_options(
        tenant_ids: Iterable[Any], *, public_catalog_changed: bool = False,
) -> None:
    try:
        redis = get_thread_safe_sync_redis()
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
