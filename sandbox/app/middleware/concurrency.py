"""Concurrency control middleware"""
import asyncio
from fastapi import HTTPException, status

from app.config import get_config
from app.models import error_response


# Global semaphores
_worker_semaphore: None | asyncio.Semaphore = None
_request_counter = 0
_request_lock = asyncio.Lock()


def init_concurrency_control():
    """Initialize concurrency control"""
    global _worker_semaphore
    config = get_config()
    _worker_semaphore = asyncio.Semaphore(config.max_workers)


async def check_max_requests():
    """Check if max requests limit is reached"""
    global _request_counter
    config = get_config()
    
    async with _request_lock:
        if _request_counter >= config.max_requests:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=error_response(-503, "Too many requests")
            )
        _request_counter += 1
    
    try:
        yield
    finally:
        async with _request_lock:
            _request_counter -= 1


async def acquire_worker():
    """Acquire a worker slot"""
    if _worker_semaphore is None:
        init_concurrency_control()
    
    async with _worker_semaphore:
        yield
