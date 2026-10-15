"""
Redis query-result cache decorator.

Provides the ``@redis_cache`` decorator, which automatically caches async/sync
function return values in Redis.

Usage::

    from .redis_cache import redis_cache, invalidate_cache

    # skip_args / id_arg accept either a parameter name or a positional index;
    # the two are equivalent. Prefer parameter names: readable and independent
    # of argument order.

    # skip_args: excluded from cache-key computation (connection objects such as
    # db are usually skipped)
    @redis_cache(ttl=300, prefix="forget_logs", skip_args=["db"])
    async def get_forget_logs(db, end_user_id, page=1, pagesize=10):
        ...

    # Equivalent form: positional index (skip_args=[0] behaves like skip_args=["db"])
    @redis_cache(ttl=120, prefix="user", skip_args=[0])
    async def get_user(db, user_id):
        ...

    # id_arg: embeds a parameter value into the key prefix for precise batch
    # invalidation by id
    @redis_cache(ttl=60, prefix="quota_breakdown", id_arg="end_user_id")
    async def get_quota_breakdown(end_user_id):
        ...

    # Invalidate every cache entry under this end_user_id
    await invalidate_cache(prefix=f"quota_breakdown:{end_user_id}")

    # Custom key building (full takeover; skip_args / id_arg no longer apply)
    @redis_cache(ttl=60, key_builder=lambda *a, **kw: f"custom:{kw['user_id']}")
    async def expensive_query(user_id):
        ...

    # return_type: names a Pydantic model; values are serialized and rebuilt
    # automatically
    @redis_cache(ttl=300, prefix="mem_cfg", skip_args=["self"],
                 return_type=MemoryConfig)
    async def load_config(self, config_id) -> MemoryConfig:
        ...

    # Without return_type, plain dict/list comes back (CacheJSONEncoder handles
    # internal types automatically)
    @redis_cache(ttl=300, prefix="auto")
    async def get_dict(self, ...) -> dict:
        ...

Arguments are normalized by signature (``sig.bind``) before the key is computed, so
positional and keyword forms (``f(db, 1)`` and ``f(db, user_id=1)``) hit the same
cache entry.

Key format:
- Without id_arg: ``cache:{prefix}:{qualname}:{args_hash}``
- With id_arg: ``cache:{prefix}:{id_value}:{qualname}:{args_hash}``
"""

import asyncio
import dataclasses
import inspect
import json
import logging
import threading
import random
import uuid
from collections import OrderedDict
from enum import Enum
from functools import wraps
from typing import Any, Callable, Iterable

import orjson
import xxhash

from ..aio_redis import get_thread_safe_redis, get_thread_safe_sync_redis

logger = logging.getLogger(__name__)

DEFAULT_TTL = 300

_in_flight: dict[str, asyncio.Task] = {}
_in_flight_lock = asyncio.Lock()

# Bounded per-key locks: cache_key cardinality (users x languages) can be huge, so an
# unbounded dict leaks a Lock per distinct key forever. Old, uncontended locks are
# evicted once the bound is reached.
_sync_locks: "OrderedDict[str, threading.Lock]" = OrderedDict()
_sync_locks_guard = threading.Lock()
_SYNC_LOCKS_MAX_SIZE = 2000


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
    """Resolve positional indices (int) and parameter names (str) in ``values`` into one set of parameter names.

    Out-of-range int indices are ignored; when the signature cannot be introspected
    (e.g. built-in functions) only str names are kept.
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
    """Convert args/kwargs into a JSON-serializable list for hash computation."""
    result: list[Any] = []
    for v in args:
        result.append(_to_hashable(v))
    if kwargs:
        result.append(dict(sorted((k, _to_hashable(v)) for k, v in kwargs.items())))
    return result


def _to_hashable(v: Any) -> Any:
    """Convert a single value into a hashable form."""
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
    """Redis cache decorator for function return values; supports sync and async functions.

    Args:
        ttl: Cache expiry in seconds; defaults to 300.
        prefix: Cache key prefix; the final key is ``cache:{prefix}:...``.
        skip_args: Parameters excluded from key computation. Accepts positional
                   indices (int) or parameter names (str).
        id_arg: Parameter embedded into the key prefix; accepts a positional index
                (int) or parameter name (str). Once set, the key format becomes
                ``cache:{prefix}:{id_value}:{qualname}:{hash}``, which can be removed
                precisely with ``invalidate_cache(prefix=f"{prefix}:{value}")``.
        key_builder: Custom key builder function.
        cache_none: Whether to cache ``None`` return values.
        return_type: Return value type. Supports Pydantic models (rebuilt on
                     deserialization via ``model_validate(data)``) and ``uuid.UUID``
                     (rebuilt from a string). dataclasses are not supported (raises
                     TypeError). When omitted, returns a JSON dict/list.
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
            elif isinstance(return_type, type) and issubclass(return_type, uuid.UUID):
                def _deserializer(data):
                    return uuid.UUID(str(data))
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
                    arguments = bound.arguments  # unified name -> value mapping
                    if _id_name is not None and _id_name in arguments:
                        id_value = str(arguments[_id_name])
                    raw: Any = {
                        k: _to_hashable(v)
                        for k, v in arguments.items()
                        if k not in _skip_names and k != _id_name
                    }
                except TypeError:
                    # Fall back to raw args/kwargs when bind fails (signature mismatch)
                    raw = _make_hashable(args, kwargs)
            else:
                # Fallback when the signature cannot be introspected
                raw = _make_hashable(args, kwargs)

            payload = json.dumps(raw, sort_keys=True, ensure_ascii=False, default=str)
            digest = xxhash.xxh64(payload.encode()).hexdigest()
            if id_value is not None:
                return f"cache:{prefix}:{id_value}:{fn.__qualname__}:{digest}"
            return f"cache:{prefix}:{fn.__qualname__}:{digest}"

        async def _cache_read_write(cache_key: str, compute):
            """Shared cache read -> fallback compute -> write logic (async)."""
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
                        # Evict oldest uncontended locks to keep the dict bounded.
                        # Scan at most len() entries; if every lock is held, abort
                        # rather than spinning (the next call retries eviction).
                        scanned = 0
                        while len(_sync_locks) > _SYNC_LOCKS_MAX_SIZE and scanned < len(_sync_locks):
                            old_key, old_lock = next(iter(_sync_locks.items()))
                            scanned += 1
                            if old_lock.locked():
                                # Skip an in-use lock and try the next entry.
                                _sync_locks.move_to_end(old_key)
                                continue
                            _sync_locks.pop(old_key)
                    else:
                        _sync_locks.move_to_end(cache_key)

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


def _resolve_invalidation(
        key: str | None,
        prefix: str | None,
        pattern: str | None,
        batch_size: int,
) -> str | None:
    """Shared argument validation and match-pattern resolution.

    Returns ``None`` when ``key`` is a precise delete; otherwise returns the usable
    glob match pattern.

    Raises:
        ValueError: ``batch_size <= 0``, or no match condition was provided.
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
    """Async SCAN + UNLINK batch delete; returns the number of deleted keys."""
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
    """Sync SCAN + UNLINK batch delete; returns the number of deleted keys."""
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
    """Actively delete cache entries.

    Three call forms::

        await invalidate_cache(key="cache:user:get_user:abc123")
        await invalidate_cache(prefix="user")           # delete every key under cache:user:*
        await invalidate_cache(pattern="cache:user:*")  # custom glob pattern

    Args:
        key: Delete a single exact key.
        prefix: Delete all keys starting with ``cache:{prefix}:*``.
        pattern: Custom glob match pattern.
        batch_size: Max keys deleted per batch, so large deletions do not block Redis.
                    Defaults to 1000.

    Returns:
        The number of deleted keys.

    Raises:
        ValueError: No match condition was provided, or batch_size <= 0.
    """
    search = _resolve_invalidation(key, prefix, pattern, batch_size)
    redis = get_thread_safe_redis()

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
    """Synchronous active cache deletion; arguments mirror ``invalidate_cache``."""
    search = _resolve_invalidation(key, prefix, pattern, batch_size)
    redis = get_thread_safe_sync_redis()

    if key is not None:
        return redis.delete(key)

    deleted = _scan_unlink_sync(redis, search, batch_size)
    if deleted:
        logger.info("Invalidated %d cache keys matching '%s'", deleted, search)
    return deleted


def invalidate_runtime_model_info(model_id: Any) -> int:
    """Synchronously clear the runtime model info cache of one model config (all tenants).

    Runtime cache keys look like ``runtime_model_info:{model_id}:{tenant_id or '_'}``;
    called after a model is disabled/deleted/updated or its API key changes, so that
    disabling takes effect immediately.
    """
    return invalidate_cache_sync(pattern=f"runtime_model_info:{model_id}:*")


async def invalidate_runtime_model_info_async(model_id: Any) -> int:
    """Asynchronously clear the runtime model info cache of one model config (all tenants)."""
    return await invalidate_cache(pattern=f"runtime_model_info:{model_id}:*")


def invalidate_runtime_model_info_batch(
    model_ids: Iterable[Any], tenant_id: Any | None = None
) -> int:
    """Batch-clear the runtime cache of several model configs (called after reverse-looking up the impact of a channel change; a single DEL).

    When tenant_id is known, delete that tenant's keys exactly plus the `:_` variant
    (defending against legacy keys written with tenant=None); when unknown, delete
    only `:_`. No SCAN is involved, so it is safe to call frequently on write paths.
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
    return get_thread_safe_sync_redis().delete(*keys)


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
