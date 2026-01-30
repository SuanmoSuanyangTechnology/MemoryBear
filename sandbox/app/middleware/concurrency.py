"""
Concurrency control middleware
"""
import asyncio
from contextlib import asynccontextmanager

from fastapi import HTTPException, status

from app.config import get_config
from app.logger import get_logger

logger = get_logger()


class ConcurrencyController:
    def __init__(self):
        self._worker_semaphore: asyncio.Semaphore | None = None
        self._request_counter = 0
        self._lock = asyncio.Lock()

        config = get_config()
        self.max_requests = config.max_requests

    def init(self):
        config = get_config()
        self._worker_semaphore = asyncio.Semaphore(config.max_workers)

    async def _acquire_worker(self):
        if self._worker_semaphore is None:
            self.init()
        async with self._worker_semaphore:
            yield

    async def _limit_requests(self):
        async with self._lock:
            logger.info(f"Current requests: {self._request_counter}/{self.max_requests}")
            if self._request_counter >= self.max_requests:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "code": 503,
                        "message": "Too many requests",
                        "data": None,
                    }
                )
            self._request_counter += 1
        try:
            yield
        finally:
            async with self._lock:
                self._request_counter -= 1

    def acquire_worker(self):
        return asynccontextmanager(self._acquire_worker)()

    def limit_requests(self):
        return asynccontextmanager(self._limit_requests)()


concurrency = ConcurrencyController()


async def concurrency_guard():
    async with concurrency.limit_requests():
        async with concurrency.acquire_worker():
            yield
