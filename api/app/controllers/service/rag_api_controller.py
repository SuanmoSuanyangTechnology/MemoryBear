"""RAG 服务接口 - 基于 API Key 认证"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.core.response_utils import success
from app.core.logging_config import get_business_logger

router = APIRouter(prefix="/knowledge", tags=["V1 - RAG API"])
logger = get_business_logger()


@router.get("")
async def list_knowledge():
    """列出可访问的知识库（占位）"""
    return success(data=[], msg="RAG API - Coming Soon")
