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


__all__ = ["get_encoder", "num_tokens_from_string", "truncate"]
