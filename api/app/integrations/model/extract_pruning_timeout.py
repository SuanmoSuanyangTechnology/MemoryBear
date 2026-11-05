"""新模型单次调用的总超时控制，与原 LLM 的分阶段超时独立。"""

import asyncio
import math
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from app.core.config import settings


@asynccontextmanager
async def extract_pruning_timeout() -> AsyncIterator[None]:
    """按环境配置限制单次调用总耗时；超时和取消交由调用方处理。"""
    timeout = settings.EXTRACT_PRUNING_REQUEST_TIMEOUT_SECONDS
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("request timeout must be finite and positive")
    async with asyncio.timeout(timeout):
        yield
