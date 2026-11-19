from fastapi import APIRouter
from sqlalchemy import text

from ...db import get_async_db_context
from ...response import ok

router = APIRouter(prefix="/internal/v1/health", tags=["health"])


@router.get("/live")
async def live():
    return ok({"status": "ok"})


@router.get("/ready")
async def ready():
    async with get_async_db_context() as session:
        await session.execute(text("SELECT 1"))
    return ok({"status": "ready"})
