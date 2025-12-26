"""Memory 服务接口 - 基于 API Key 认证"""

from app.core.api_key_auth import require_api_key
from app.core.logging_config import get_business_logger
from app.core.response_utils import success
from app.db import get_db
from app.schemas.api_key_schema import ApiKeyAuth
from app.schemas.memory_api_schema import (
    MemoryReadRequest,
    MemoryReadResponse,
    MemoryWriteRequest,
    MemoryWriteResponse,
)
from app.services.memory_api_service import MemoryAPIService
from fastapi import APIRouter, Body, Depends, Request
from sqlalchemy.orm import Session

router = APIRouter(prefix="/memory", tags=["V1 - Memory API"])
logger = get_business_logger()


@router.get("")
async def get_memory_info():
    """获取记忆服务信息（占位）"""
    return success(data={}, msg="Memory API - Coming Soon")


@router.post("/write_api_service")
@require_api_key(scopes=["memory"])
async def write_memory_api_service(
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
    payload: MemoryWriteRequest = Body(..., embed=False),

):
    """
    Write memory to storage.
    
    Stores memory content for the specified end user using the Memory API Service.
    """
    logger.info(f"Memory write request - end_user_id: {payload.end_user_id}")
    
    memory_api_service = MemoryAPIService(db)
    
    result = await memory_api_service.write_memory(
        workspace_id=api_key_auth.workspace_id,
        end_user_id=payload.end_user_id,
        message=payload.message,
        config_id=payload.config_id,
        storage_type=payload.storage_type,
        user_rag_memory_id=payload.user_rag_memory_id,
    )
    
    logger.info(f"Memory write successful for end_user: {payload.end_user_id}")
    return success(data=MemoryWriteResponse(**result).model_dump(), msg="Memory written successfully")


@router.post("/read_api_service")
@require_api_key(scopes=["memory"])
async def read_memory_api_service(
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
    payload: MemoryReadRequest = Body(..., embed=False),
):
    """
    Read memory from storage.
    
    Queries and retrieves memories for the specified end user with context-aware responses.
    """
    logger.info(f"Memory read request - end_user_id: {payload.end_user_id}")
    
    memory_api_service = MemoryAPIService(db)
    
    result = await memory_api_service.read_memory(
        workspace_id=api_key_auth.workspace_id,
        end_user_id=payload.end_user_id,
        message=payload.message,
        search_switch=payload.search_switch,
        config_id=payload.config_id,
        storage_type=payload.storage_type,
        user_rag_memory_id=payload.user_rag_memory_id,
    )
    
    logger.info(f"Memory read successful for end_user: {payload.end_user_id}")
    return success(data=MemoryReadResponse(**result).model_dump(), msg="Memory read successfully")
