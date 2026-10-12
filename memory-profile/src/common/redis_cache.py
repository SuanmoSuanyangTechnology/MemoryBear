"""Async Redis cache-aside helpers shared by Memory modules."""

from __future__ import annotations

import asyncio
import dataclasses
import inspect
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from enum import Enum
from functools import wraps
from typing import Any
from uuid import UUID

import xxhash
from pydantic import BaseModel
from redis.asyncio import Redis
from redis.exceptions import RedisError

DEFAULT_TTL = 300
CACHE_MISS = object()
RedisClientProvider = Callable[[], Awaitable[Redis]]
logger = logging.getLogger(__name__)
_client_provider: RedisClientProvider | None = None
_in_flight: dict[tuple[int, str], asyncio.Task[Any]] = {}


class RedisCacheError(RuntimeError):
    """Base error for explicit cache operations."""


class RedisCacheUnavailableError(RedisCacheError):
    def __init__(self, operation: str, key: str) -> None:
        super().__init__(f"Redis cache unavailable: operation={operation} key={key}")
        self.operation = operation
        self.key = key


class RedisCacheDecodeError(RedisCacheError):
    def __init__(self, key: str, *, member_index: int | None = None) -> None:
        location = f" member_index={member_index}" if member_index is not None else ""
        super().__init__(f"Invalid Redis cache JSON: key={key}{location}")
        self.key = key
        self.member_index = member_index


def configure_redis_cache(client_provider: RedisClientProvider) -> None:
    """Bind helpers to the process-owned ``ProfileRuntime.redis`` provider."""

    global _client_provider
    _client_provider = client_provider


def clear_redis_cache() -> None:
    global _client_provider
    _client_provider = None


def _provider(override: RedisClientProvider | None = None) -> RedisClientProvider:
    provider = override or _client_provider
    if provider is None:
        raise RuntimeError("Redis cache client provider is not configured")
    return provider


def _decode_json(
    value: object,
    *,
    key: str,
    member_index: int | None = None,
) -> Any:
    try:
        if isinstance(value, bytes):
            value = value.decode("utf-8", errors="strict")
        if not isinstance(value, str):
            raise TypeError("Redis cache value must be str or bytes")
        return json.loads(value)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RedisCacheDecodeError(key, member_index=member_index) from exc


def _json_default(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, set | frozenset):
        return list(value)
    raise TypeError(f"Unsupported Redis cache JSON type: {type(value).__name__}")


async def get_json(
    key: str,
    *,
    client_provider: RedisClientProvider | None = None,
) -> Any:
    try:
        value = await (await _provider(client_provider)()).get(key)
    except RedisError as exc:
        raise RedisCacheUnavailableError("get", key) from exc
    if value is None:
        return CACHE_MISS
    return _decode_json(value, key=key)


async def set_json(
    key: str,
    value: Any,
    ttl: int,
    *,
    client_provider: RedisClientProvider | None = None,
) -> bool:
    if ttl <= 0:
        raise ValueError("ttl must be positive")
    payload = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        default=_json_default,
    )
    try:
        result = await (await _provider(client_provider)()).set(key, payload, ex=ttl)
    except RedisError as exc:
        raise RedisCacheUnavailableError("set", key) from exc
    return bool(result)


async def delete_json(
    key: str,
    *,
    client_provider: RedisClientProvider | None = None,
) -> int:
    try:
        return int(await (await _provider(client_provider)()).delete(key))
    except RedisError as exc:
        raise RedisCacheUnavailableError("delete", key) from exc


async def zrange_json(
    key: str,
    start: int = 0,
    stop: int = -1,
    *,
    desc: bool = False,
    client_provider: RedisClientProvider | None = None,
) -> list[Any]:
    try:
        members = await (await _provider(client_provider)()).zrange(
            key,
            start,
            stop,
            desc=desc,
        )
    except RedisError as exc:
        raise RedisCacheUnavailableError("zrange", key) from exc
    return [
        _decode_json(member, key=key, member_index=index) for index, member in enumerate(members)
    ]


def _resolve_invalidation(
    key: str | None,
    prefix: str | None,
    pattern: str | None,
    batch_size: int,
) -> str | None:
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")
    if key is not None:
        return None
    search = pattern or (f"cache:{prefix}:*" if prefix else None)
    if search is None:
        raise ValueError("Provide key=, prefix=, or pattern=")
    return search


async def invalidate_cache(
    key: str | None = None,
    *,
    prefix: str | None = None,
    pattern: str | None = None,
    batch_size: int = 1000,
    client_provider: RedisClientProvider | None = None,
) -> int:
    """Match the API helper: exact DEL or prefix/pattern SCAN + batched UNLINK."""

    search = _resolve_invalidation(key, prefix, pattern, batch_size)
    client = await _provider(client_provider)()
    if key is not None:
        try:
            return int(await client.delete(key))
        except RedisError as exc:
            raise RedisCacheUnavailableError("delete", key) from exc

    scan_hint = max(1, min(batch_size, 500))
    cursor = 0
    deleted = 0
    try:
        while True:
            cursor, keys = await client.scan(cursor, match=search, count=scan_hint)
            for index in range(0, len(keys), batch_size):
                deleted += int(await client.unlink(*keys[index : index + batch_size]))
            if int(cursor) == 0:
                break
    except RedisError as exc:
        raise RedisCacheUnavailableError("scan_unlink", str(search)) from exc
    if deleted:
        logger.info("Invalidated %d cache keys pattern=%s", deleted, search)
    return deleted


def _resolve_parameter_names(
    function: Callable[..., Any],
    values: list[int | str],
) -> frozenset[str]:
    parameters = list(inspect.signature(function).parameters)
    names: set[str] = set()
    for value in values:
        if isinstance(value, int):
            if 0 <= value < len(parameters):
                names.add(parameters[value])
        else:
            names.add(value)
    return frozenset(names)


def _to_hashable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, bytes | bytearray):
        return bytes(value).hex()
    if isinstance(value, set | frozenset):
        return sorted((_to_hashable(item) for item in value), key=repr)
    if isinstance(value, dict):
        return {str(key): _to_hashable(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_to_hashable(item) for item in value]
    return value


def redis_cache(
    ttl: int = DEFAULT_TTL,
    prefix: str = "default",
    skip_args: list[int | str] | None = None,
    id_arg: int | str | None = None,
    key_builder: Callable[[str, Callable[..., Any], tuple[Any, ...], dict[str, Any]], str]
    | None = None,
    cache_none: bool = False,
    return_type: type | None = None,
    client_provider: RedisClientProvider | None = None,
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """Cache an async function with API-compatible key and failure semantics."""

    if ttl <= 0:
        raise ValueError("redis_cache ttl must be positive")

    def decorate(function: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        if not inspect.iscoroutinefunction(function):
            raise TypeError("redis_cache only supports async functions")
        if return_type is not None and dataclasses.is_dataclass(return_type):
            raise TypeError("redis_cache return_type does not support dataclasses")
        function_signature = inspect.signature(function)
        skipped = _resolve_parameter_names(function, skip_args or [])
        id_name = (
            next(iter(_resolve_parameter_names(function, [id_arg])), None)
            if id_arg is not None
            else None
        )

        def build_key(
            _function: Callable[..., Any],
            args: tuple[Any, ...],
            kwargs: dict[str, Any],
        ) -> str:
            if key_builder is not None:
                return key_builder(prefix, function, args, kwargs)
            bound = function_signature.bind(*args, **kwargs)
            bound.apply_defaults()
            arguments = bound.arguments
            identifier = arguments.get(id_name) if id_name is not None else None
            key_arguments = {
                name: _to_hashable(value)
                for name, value in arguments.items()
                if name not in skipped and name != id_name
            }
            payload = json.dumps(
                key_arguments,
                sort_keys=True,
                ensure_ascii=False,
                default=str,
            )
            digest = xxhash.xxh64(payload.encode()).hexdigest()
            if identifier is not None:
                return f"cache:{prefix}:{identifier}:{function.__qualname__}:{digest}"
            return f"cache:{prefix}:{function.__qualname__}:{digest}"

        def deserialize(value: Any) -> Any:
            if return_type is None:
                return value
            if isinstance(return_type, type) and issubclass(return_type, BaseModel):
                return return_type.model_validate(value)
            if isinstance(return_type, type) and issubclass(return_type, UUID):
                return UUID(str(value))
            raise TypeError("redis_cache return_type must be a Pydantic model or UUID")

        @wraps(function)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                provider = _provider(client_provider)
                cache_key = build_key(function, args, kwargs)
            except (RuntimeError, TypeError, ValueError):
                logger.warning(
                    "Redis cache setup failed for %s",
                    function.__qualname__,
                    exc_info=True,
                )
                return await function(*args, **kwargs)

            try:
                cached = await get_json(cache_key, client_provider=provider)
            except RedisCacheError:
                logger.warning("Redis cache read failed key=%s", cache_key, exc_info=True)
                cached = CACHE_MISS
            if cached is not CACHE_MISS:
                try:
                    return deserialize(cached)
                except (TypeError, ValueError):
                    logger.warning(
                        "Redis cache type restoration failed key=%s",
                        cache_key,
                        exc_info=True,
                    )

            async def compute_and_store() -> Any:
                result = await function(*args, **kwargs)
                if result is None and not cache_none:
                    return result
                try:
                    await set_json(cache_key, result, ttl, client_provider=provider)
                except (RedisCacheError, TypeError, ValueError):
                    logger.warning(
                        "Redis cache write failed key=%s",
                        cache_key,
                        exc_info=True,
                    )
                return result

            task_key = (id(asyncio.get_running_loop()), cache_key)
            task = _in_flight.get(task_key)
            if task is None or task.done():
                task = asyncio.create_task(compute_and_store())
                _in_flight[task_key] = task

                def cleanup(completed: asyncio.Task[Any]) -> None:
                    if _in_flight.get(task_key) is completed:
                        _in_flight.pop(task_key, None)

                task.add_done_callback(cleanup)
            return await asyncio.shield(task)

        wrapper._cache_prefix = prefix  # type: ignore[attr-defined]
        wrapper._cache_ttl = ttl  # type: ignore[attr-defined]
        wrapper._cache_build_key = build_key  # type: ignore[attr-defined]
        return wrapper

    return decorate
