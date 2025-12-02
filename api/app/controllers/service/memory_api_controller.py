"""Memory 服务接口 - 基于 API Key 认证"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.core.response_utils import success
from app.core.logging_config import get_business_logger

router = APIRouter(prefix="/memory", tags=["V1 - Memory API"])
logger = get_business_logger()


@router.get("")
async def get_memory_info():
    """获取记忆服务信息（占位）"""
    return success(data={}, msg="Memory API - Coming Soon")
