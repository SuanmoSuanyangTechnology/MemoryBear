"""Sandbox API endpoints"""
from fastapi import APIRouter, Depends

from app.middleware.auth import verify_api_key
from app.middleware.concurrency import check_max_requests, acquire_worker
from app.models import (
    RunCodeRequest,
    ApiResponse,
    UpdateDependencyRequest,
    error_response
)
from app.services.python_service import (
    run_python_code,
    list_python_dependencies,
    update_python_dependencies
)

router = APIRouter(
    prefix="/v1/sandbox",
    tags=["sandbox"],
    dependencies=[Depends(verify_api_key)]
)


@router.post(
    "/run",
    response_model=ApiResponse,
    dependencies=[Depends(check_max_requests),
                  Depends(acquire_worker)]
)
async def run_code(request: RunCodeRequest):
    """Execute code in sandbox"""
    if request.language == "python3":
        return await run_python_code(request.code, request.preload, request.options)
    elif request.language == "nodejs":
        # TODO
        return error_response(-400, "TODO")
    else:
        return error_response(-400, "unsupported language")


@router.get("/dependencies", response_model=ApiResponse)
async def get_dependencies(language: str):
    """Get installed dependencies"""
    if language == "python3":
        return await list_python_dependencies()
    else:
        return error_response(-400, "unsupported language")


@router.post("/dependencies/update", response_model=ApiResponse)
async def update_dependencies(request: UpdateDependencyRequest):
    """Update dependencies"""
    if request.language == "python3":
        return await update_python_dependencies()
    else:
        return error_response(-400, "unsupported language")


