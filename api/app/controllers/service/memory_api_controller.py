"""Memory 服务接口 - 基于 API Key 认证"""
import uuid

from fastapi import APIRouter, Depends, Request, Body
from sqlalchemy.orm import Session

from app.db import get_db
from app.core.response_utils import success
from app.core.logging_config import get_business_logger
from app.core.api_key_auth import require_api_key
from app.schemas.api_key_schema import ApiKeyAuth

router = APIRouter(prefix="/memory", tags=["V1 - Memory API"])
logger = get_business_logger()


@router.get("")
async def get_memory_info():
    """获取记忆服务信息（占位）"""
    return success(data={}, msg="Memory API - Coming Soon")


# /v1/memory/chat
@router.post("/chat")
@require_api_key(scopes=["memory"])
async def chat_with_agent_demo(
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
    message: str = Body(..., description="聊天消息内容"),
):
    """
    Agent 聊天接口demo

    scopes: 所需的权限范围列表["app", "rag", "memory"]

    Args:
        message: 请求参数
        request: 声明请求
        api_key_auth: 包含验证后的API Key 信息
        db: db_session
    """
    logger.info(f"API Key Auth: {api_key_auth}")
    logger.info(f"Resource ID: {api_key_auth.resource_id}")
    logger.info(f"Message: {message}")
    return success(data={"received": True}, msg="消息已接收")