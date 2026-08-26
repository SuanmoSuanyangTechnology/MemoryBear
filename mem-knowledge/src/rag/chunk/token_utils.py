from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=1)
def get_encoder():
    """Load the ingestion encoder on first token-counting use."""

    import tiktoken

    return tiktoken.get_encoding("cl100k_base")


def num_tokens_from_string(value: str) -> int:
    try:
        return len(get_encoder().encode(value))
    except Exception:
        return 0


def truncate(value: str, max_len: int) -> str:
    encoder = get_encoder()
    return encoder.decode(encoder.encode(value)[:max_len])


def split_by_token_limit(value: str, max_tokens: int) -> list[str]:
    """Split text without losing characters while keeping each part within a token limit."""

    if max_tokens < 1:
        raise ValueError("max_tokens must be at least 1")
    encoder = get_encoder()
    if len(encoder.encode(value)) <= max_tokens:
        return [value] if value else []

    chunks: list[str] = []
    start = 0
    while start < len(value):
        low = start + 1
        high = len(value)
        end = low
        while low <= high:
            middle = (low + high) // 2
            if len(encoder.encode(value[start:middle])) <= max_tokens:
                end = middle
                low = middle + 1
            else:
                high = middle - 1
        chunks.append(value[start:end])
        start = end
    return chunks


__all__ = ["get_encoder", "num_tokens_from_string", "split_by_token_limit", "truncate"]
