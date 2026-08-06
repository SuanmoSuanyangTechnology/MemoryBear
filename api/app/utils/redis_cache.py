"""
Redis 查询结果缓存装饰器

提供 ``@redis_cache`` 装饰器，自动缓存异步/同步函数的返回值到 Redis。

用法::

    from app.utils.redis_cache import redis_cache, invalidate_cache

    # skip_args / id_arg 都支持「参数名」或「位置索引」，二者等价。
    # 推荐用参数名，可读且不依赖参数顺序。

    # skip_args：不参与缓存 key 计算（db 等连接对象通常要跳过）
    @redis_cache(ttl=300, prefix="forget_logs", skip_args=["db"])
    async def get_forget_logs(db, end_user_id, page=1, pagesize=10):
        ...

    # 等价写法：位置索引（skip_args=[0] 与 skip_args=["db"] 效果相同）
    @redis_cache(ttl=120, prefix="user", skip_args=[0])
    async def get_user(db, user_id):
        ...

    # id_arg：把某个参数值嵌入 key 前缀，便于按 id 精确批量失效
    @redis_cache(ttl=60, prefix="quota_breakdown", id_arg="end_user_id")
    async def get_quota_breakdown(end_user_id):
        ...

    # 失效该 end_user_id 下的全部缓存条目
    await invalidate_cache(prefix=f"quota_breakdown:{end_user_id}")

    # 自定义 key 构建（完全接管，skip_args / id_arg 不生效）
    @redis_cache(ttl=60, key_builder=lambda *a, **kw: f"custom:{kw['user_id']}")
    async def expensive_query(user_id):
        ...

    # return_type：指定 Pydantic model，自动序列化并重建对象
    @redis_cache(ttl=300, prefix="mem_cfg", skip_args=["self"],
                 return_type=MemoryConfig)
    async def load_config(self, config_id) -> MemoryConfig:
        ...

    # 不指定 return_type 时返回普通 dict/list（CacheJSONEncoder 自动处理内部类型）
    @redis_cache(ttl=300, prefix="auto")
    async def get_dict(self, ...) -> dict:
        ...

参数按签名归一化（``sig.bind``）后再计算 key，因此位置传参与关键字传参
（``f(db, 1)`` 与 ``f(db, user_id=1)``）会命中同一个缓存。

Key 格式：
- 无 id_arg： ``cache:{prefix}:{qualname}:{args_hash}``
- 有 id_arg： ``cache:{prefix}:{id_value}:{qualname}:{args_hash}``
"""

import asyncio
import dataclasses
import inspect
import json
import logging
import threading
import random
import uuid
from enum import Enum
from functools import wraps
from typing import Any, Callable, Iterable

import orjson
import xxhash

from app.aioRedis import get_thread_safe_redis, get_thread_safe_sync_redis

logger = logging.getLogger(__name__)

DEFAULT_TTL = 300

_in_flight: dict[str, asyncio.Task] = {}
_in_flight_lock = asyncio.Lock()

_sync_locks: dict[str, threading.Lock] = {}
_sync_locks_guard = threading.Lock()


def _orjson_default(o: Any) -> Any:
    if isinstance(o, uuid.UUID):
        return str(o)
    if hasattr(o, "model_dump"):
        return o.model_dump(mode="json")
    if isinstance(o, set):
        return list(o)
    if hasattr(o, "to_dict") and callable(o.to_dict):
        return o.to_dict()
    if hasattr(o, "dict") and callable(o.dict):
        return o.dict()
    raise TypeError(f"Type not serializable: {type(o).__name__}")


def _resolve_param_names(
        func: Callable,
        values: list[int | str],
) -> frozenset[str]:
    """将 ``values`` 中的位置索引 (int) 与参数名 (str) 统一解析为参数名集合。

    越界的 int 索引会被忽略；无法内省签名时（如内置函数）只保留 str 参数名。
    """
    try:
        params = list(inspect.signature(func).parameters)
    except (ValueError, TypeError):
        params = []

    names: set[str] = set()
    for v in values:
        if isinstance(v, int):
            if 0 <= v < len(params):
                names.add(params[v])
        else:
            names.add(v)
    return frozenset(names)


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
        id_arg: int | str | None = None,
        key_builder: Callable[..., str] | None = None,
        cache_none: bool = False,
        return_type: type | None = None,
) -> Callable:
    """函数返回值 Redis 缓存装饰器，同时支持同步和异步函数。

    Args:
        ttl: 缓存过期时间（秒），默认 300。
        prefix: 缓存 key 前缀，最终 key 为 ``cache:{prefix}:...``。
        skip_args: 不参与 key 计算的参数。支持位置索引 (int) 或参数名 (str)。
        id_arg: 嵌入 key 前缀的参数，支持位置索引 (int) 或参数名 (str)。
                设置后 key 格式变为 ``cache:{prefix}:{id_value}:{qualname}:{hash}``，
                可通过 ``invalidate_cache(prefix=f"{prefix}:{value}")`` 精确清除。
        key_builder: 自定义 key 构建函数。
        cache_none: 是否缓存 ``None`` 返回值。
        return_type: 返回值 Pydantic model 类型。
                     序列化时自动调 ``model_dump(mode='json')``，
                     反序列化时自动调 ``model_validate(data)`` 重建对象。
                     不支持 dataclass（会抛 TypeError）。
                     未指定时返回 JSON dict/list。
    """

    def deco(func: Callable):
        try:
            sig = inspect.signature(func)
        except (ValueError, TypeError):
            sig = None

        _skip_names = _resolve_param_names(func, skip_args or [])
        _id_name: str | None = None
        if id_arg is not None:
            _id_name = next(iter(_resolve_param_names(func, [id_arg])), None)

        _deserializer = None
        if return_type is not None:
            if hasattr(return_type, "model_validate"):
                def _deserializer(data):
                    return return_type.model_validate(data)
            elif dataclasses.is_dataclass(return_type):
                raise TypeError(
                    f"return_type={return_type.__name__} is a dataclass, not supported. "
                    f"Use a Pydantic BaseModel instead."
                )

        def _build_key(fn: Callable, args: tuple, kwargs: dict) -> str:
            if key_builder is not None:
                return key_builder(prefix, fn, args, kwargs)

            id_value: str | None = None
            if sig is not None:
                try:
                    bound = sig.bind(*args, **kwargs)
                    bound.apply_defaults()
                    arguments = bound.arguments  # 统一的 name -> value
                    if _id_name is not None and _id_name in arguments:
                        id_value = str(arguments[_id_name])
                    raw: Any = {
                        k: _to_hashable(v)
                        for k, v in arguments.items()
                        if k not in _skip_names and k != _id_name
                    }
                except TypeError:
                    # bind 失败（签名不匹配）时回退到原始 args/kwargs
                    raw = _make_hashable(args, kwargs)
            else:
                # 无法内省签名时的回退
                raw = _make_hashable(args, kwargs)

            payload = json.dumps(raw, sort_keys=True, ensure_ascii=False, default=str)
            digest = xxhash.xxh64(payload.encode()).hexdigest()
            if id_value is not None:
                return f"cache:{prefix}:{id_value}:{fn.__qualname__}:{digest}"
            return f"cache:{prefix}:{fn.__qualname__}:{digest}"

        async def _cache_read_write(cache_key: str, compute):
            """共享的缓存读取→回退计算→写入逻辑（异步）。"""
            redis = get_thread_safe_redis()

            try:
                cached = await redis.get(cache_key)
            except Exception:
                logger.warning("Redis GET failed for key=%s", cache_key, exc_info=True)
                cached = None

            if cached is not None:
                try:
                    result = orjson.loads(cached)
                    if _deserializer is not None:
                        result = _deserializer(result)
                    logger.debug("Cache HIT: %s", cache_key)
                    return result
                except (orjson.JSONDecodeError, TypeError, ValueError):
                    logger.warning(
                        "Corrupted cache data for key=%s, will recompute", cache_key,
                    )

            async with _in_flight_lock:
                task = _in_flight.get(cache_key)
                if task is None or task.done():
                    task = asyncio.create_task(compute())
                    _in_flight[cache_key] = task

            try:
                if task.done():
                    result = task.result()
                else:
                    result = await task
            finally:
                async with _in_flight_lock:
                    if _in_flight.get(cache_key) is task:
                        del _in_flight[cache_key]

            if result is None and not cache_none:
                return None

            try:
                value = orjson.dumps(result, default=_orjson_default)
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
            async_wrapper._cache_build_key = _build_key
            return async_wrapper

        else:
            # sync
            @wraps(func)
            def sync_wrapper(*args, **kwargs):
                cache_key = _build_key(func, args, kwargs)
                redis = get_thread_safe_sync_redis()

                def _try_read_cache():
                    try:
                        cached = redis.get(cache_key)
                        if cached is None:
                            return None
                        result = orjson.loads(cached)
                        if _deserializer is not None:
                            result = _deserializer(result)
                        logger.debug("Cache HIT: %s", cache_key)
                        return result
                    except (orjson.JSONDecodeError, TypeError, ValueError):
                        logger.warning(
                            "Corrupted cache data for key=%s, will recompute", cache_key,
                        )
                        return None
                    except Exception:
                        logger.warning("Redis GET failed for key=%s", cache_key, exc_info=True)
                        return None

                result = _try_read_cache()
                if result is not None:
                    return result

                with _sync_locks_guard:
                    key_lock = _sync_locks.get(cache_key)
                    if key_lock is None:
                        key_lock = threading.Lock()
                        _sync_locks[cache_key] = key_lock

                with key_lock:
                    result = _try_read_cache()
                    if result is not None:
                        return result

                    result = func(*args, **kwargs)

                    if result is None and not cache_none:
                        return None

                    try:
                        value = orjson.dumps(result, default=_orjson_default)
                        redis.set(cache_key, value, ex=ttl)
                        logger.debug("Cache SET: %s (ttl=%ds)", cache_key, ttl)
                    except Exception:
                        logger.warning("Redis SET failed for key=%s", cache_key, exc_info=True)

                return result

            sync_wrapper._cache_prefix = prefix
            sync_wrapper._cache_ttl = ttl
            sync_wrapper._cache_build_key = _build_key
            return sync_wrapper

    return deco


async def invalidate_cache(
        key: str | None = None,
        *,
        prefix: str | None = None,
        pattern: str | None = None,
        batch_size: int = 1000,
) -> int:
    """主动删除缓存。

    三种调用方式::

        await invalidate_cache(key="cache:user:get_user:abc123")
        await invalidate_cache(prefix="user")           # 删除 cache:user:* 开头的所有 key
        await invalidate_cache(pattern="cache:user:*")  # 自定义 glob 模式

    Args:
        key: 精确删除单个 key。
        prefix: 删除 ``cache:{prefix}:*`` 开头的所有 key。
        pattern: 自定义 glob 匹配模式。
        batch_size: 每批最多删除的 key 数量，避免大规模删除阻塞 Redis。默认 1000。

    Returns:
        已删除的 key 数量。

    Raises:
        ValueError: 未提供任何匹配条件，或 batch_size <= 0。
    """
    if batch_size < 1:
        raise ValueError(f"batch_size must be >= 1, got {batch_size}")

    redis = get_thread_safe_redis()

    if key is not None:
        return await redis.delete(key)

    search = pattern or (f"cache:{prefix}:*" if prefix else None)
    if search is None:
        raise ValueError("Provide key=, prefix=, or pattern=")

    scan_hint = max(1, min(batch_size, 500))
    deleted = 0
    cursor = 0
    while True:
        cursor, keys = await redis.scan(cursor, match=search, count=scan_hint)
        if keys:
            for i in range(0, len(keys), batch_size):
                chunk = keys[i:i + batch_size]
                deleted += await redis.unlink(*chunk)
        if cursor == 0:
            break

    if deleted:
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
