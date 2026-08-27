"""Redis cache helpers used by parse-time automatic QA generation."""

from __future__ import annotations

from typing import Any

import xxhash

LLM_CACHE_TTL_SECONDS = 24 * 3600


def llm_cache_key(
    model_name: object,
    text: object,
    history: object,
    generation_config: object,
) -> str:
    """Build the exact legacy xxh64 cache key."""

    hasher = xxhash.xxh64()
    hasher.update(
        (str(model_name) + str(text) + str(history) + str(generation_config)).encode("utf-8")
    )
    return hasher.hexdigest()


def get_llm_cache(
    redis_client: Any,
    model_name: object,
    text: object,
    history: object,
    generation_config: object,
) -> bytes | str | None:
    return redis_client.get(llm_cache_key(model_name, text, history, generation_config))


def set_llm_cache(
    redis_client: Any,
    model_name: object,
    text: object,
    value: str,
    history: object,
    generation_config: object,
) -> None:
    redis_client.set(
        llm_cache_key(model_name, text, history, generation_config),
        value.encode("utf-8"),
        LLM_CACHE_TTL_SECONDS,
    )


__all__ = [
    "LLM_CACHE_TTL_SECONDS",
    "get_llm_cache",
    "llm_cache_key",
    "set_llm_cache",
]
