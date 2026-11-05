"""Common profile service primitives."""

from src.common.pagination import PageData, PageMeta
from src.common.redis_cache import (
    CACHE_MISS,
    RedisCacheDecodeError,
    RedisCacheError,
    RedisCacheUnavailableError,
    clear_redis_cache,
    configure_redis_cache,
    delete_json,
    get_json,
    invalidate_cache,
    redis_cache,
    set_json,
    zrange_json,
)
from src.common.responses import (
    ApiResponse,
    error_response,
    failure,
    http_error_response,
    server_error_response,
    success,
    validation_error_response,
)
from src.common.timing import now_ms, utcnow_naive

__all__ = [
    "ApiResponse",
    "CACHE_MISS",
    "PageData",
    "PageMeta",
    "RedisCacheDecodeError",
    "RedisCacheError",
    "RedisCacheUnavailableError",
    "clear_redis_cache",
    "configure_redis_cache",
    "delete_json",
    "error_response",
    "failure",
    "get_json",
    "http_error_response",
    "invalidate_cache",
    "now_ms",
    "redis_cache",
    "server_error_response",
    "set_json",
    "success",
    "utcnow_naive",
    "validation_error_response",
    "zrange_json",
]
