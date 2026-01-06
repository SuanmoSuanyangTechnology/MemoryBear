from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.response_utils import success
from app.db import get_db
from app.dependencies import get_current_user
from app.models.user_model import User
from app.schemas.response_schema import ApiResponse
from app.services.home_page_service import HomePageService

router = APIRouter(prefix="/home-page", tags=["Home Page"])

@router.get("/statistics", response_model=ApiResponse)
def get_home_statistics(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取首页统计数据"""
    statistics = HomePageService.get_home_statistics(db, current_user.tenant_id)
    return success(data=statistics, msg="统计数据获取成功")

@router.get("/workspaces", response_model=ApiResponse)
def get_workspace_list(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取工作空间列表"""
    workspace_list = HomePageService.get_workspace_list(db, current_user.tenant_id)
    return success(data=workspace_list, msg="工作空间列表获取成功")

@router.get("/version", response_model=ApiResponse)
def get_system_version():
    """获取系统版本号"""
    return success(data={"version": settings.SYSTEM_VERSION}, msg="系统版本获取成功")