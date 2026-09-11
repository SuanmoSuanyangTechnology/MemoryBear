from fastapi import APIRouter, status, Request

from src.config import settings
from src.infrastructure.logger.config import get_logger

router = APIRouter(
    prefix='/health',
    tags=['health']
)

logger = get_logger("health")


@router.get("/ready")
async def readiness(request: Request):
    print("Query:", dict(request.query_params))
    print("Headers:", dict(request.headers))
    return {"status": "ready", "read_backend": settings.MEMORY_READ_BACKEND}
