"""App 服务接口 - 基于 API Key 认证"""
import uuid
from fastapi import APIRouter, Depends, Request, Body, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.core.response_utils import success
from app.core.logging_config import get_business_logger
from app.core.api_key_auth import require_api_key
from app.schemas.api_key_schema import ApiKeyAuth

router = APIRouter(prefix="/apps", tags=["V1 - App API"])
logger = get_business_logger()


@router.get("")
async def list_apps():
    """列出可访问的应用（占位）"""
    return success(data=[], msg="App API - Coming Soon")

# /v1/apps/{resource_id}/chat
@router.post("/{resource_id}/chat")
@require_api_key(scopes=["app"])
async def chat_with_agent_demo(
    resource_id: uuid.UUID,
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
    message: str = Body(..., description="聊天消息内容"),
):
    """
    Agent 聊天接口demo

    scopes: 所需的权限范围列表["app", "rag", "memory"]

    Args:
        resource_id: 如果是应用的apikey传的是应用id; 如果是服务的apikey传的是工作空间id
        message: 请求参数
        request: 声明请求
        api_key_auth: 包含验证后的API Key 信息
        db: db_session
    """
    logger.info(f"API Key Auth: {api_key_auth}")
    logger.info(f"Resource ID: {resource_id}")
    logger.info(f"Message: {message}")
    return success(data={"received": True}, msg="消息已接收")

# /v1/apps/{resource_id}/chat
@router.get("/{resource_id}/chat")
@require_api_key(scopes=["app"])
async def chat_with_agent_demo(
    resource_id: uuid.UUID,
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
    message: str = Query(..., description="聊天消息内容"),
):
    """
    Agent 聊天接口demo

    scopes: 所需的权限范围列表["app", "rag", "memory"]

    Args:
        resource_id: 如果是应用的apikey传的是应用id; 如果是服务的apikey传的是工作空间id
        message: 请求参数
        request: 声明请求
        api_key_auth: 包含验证后的API Key 信息
        db: db_session
    """
    logger.info(f"API Key Auth: {api_key_auth}")
    logger.info(f"Resource ID: {resource_id}")
    logger.info(f"Message: {message}")
    return success(data={"received": True}, msg="消息已接收")
