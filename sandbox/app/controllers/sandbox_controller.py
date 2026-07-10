"""Sandbox API endpoints"""
import time

from fastapi import APIRouter, Depends

from app.middleware.auth import verify_api_key
from app.middleware.concurrency import queue_controller

from app.models import (
    RunCodeRequest,
    ApiResponse,
    UpdateDependencyRequest,
    error_response
)
from app.services.nodejs_service import run_nodejs_code
from app.services.python_service import (
    run_python_code,
    list_python_dependencies,
    update_python_dependencies
)
from app.logger import get_logger

logger = get_logger()

router = APIRouter(
    prefix="/v1/sandbox",
    tags=["sandbox"],
    dependencies=[Depends(verify_api_key)]
)


@router.post("/run", response_model=ApiResponse)
async def run_code(request: RunCodeRequest):
    """Execute code in sandbox (queue-based concurrency)"""
    t_enqueue = time.perf_counter()

    async def _execute():
        t_exec = time.perf_counter()
        try:
            if request.language == "python3":
                result = await run_python_code(request.code, request.preload, request.options)
            elif request.language == "javascript":
                result = await run_nodejs_code(request.code, request.preload, request.options)
            else:
                result = error_response(400, "unsupported language")
        finally:
            elapsed = (time.perf_counter() - t_exec) * 1000
            queue_wait = (t_exec - t_enqueue) * 1000
            total = elapsed + queue_wait
            logger.info(
                "request done lang=%s queue_wait=%.1fms exec=%.1fms total=%.1fms queue_depth=%d",
                request.language, queue_wait, elapsed, total,
                queue_controller.stats["queue_size"],
            )
        return result

    return await queue_controller.submit(_execute)


@router.get("/dependencies", response_model=ApiResponse)
async def get_dependencies(language: str):
    """Get installed dependencies"""
    if language == "python3":
        return await list_python_dependencies()
    else:
        return error_response(400, "unsupported language")


@router.post("/dependencies/update", response_model=ApiResponse)
async def update_dependencies(request: UpdateDependencyRequest):
    """Update dependencies"""
    if request.language == "python3":
        return await update_python_dependencies()
    else:
        return error_response(400, "unsupported language")