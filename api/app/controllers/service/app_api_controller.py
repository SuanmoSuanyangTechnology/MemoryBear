"""App 服务接口 - 基于 API Key 认证"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.core.response_utils import success
from app.core.logging_config import get_business_logger

router = APIRouter(prefix="/apps", tags=["V1 - App API"])
logger = get_business_logger()


@router.get("")
async def list_apps():
    """列出可访问的应用（占位）"""
    return success(data=[], msg="App API - Coming Soon")
