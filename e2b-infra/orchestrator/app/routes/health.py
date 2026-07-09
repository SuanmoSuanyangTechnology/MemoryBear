"""Health check routes"""
from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/health")
async def health_check(request: Request):
    """Health check endpoint"""
    sandbox_manager = request.app.state.sandbox_manager
    stats = await sandbox_manager.get_stats()
    return {
        "status": "healthy",
        "version": "1.0.0",
        "sandboxes": stats,
    }
