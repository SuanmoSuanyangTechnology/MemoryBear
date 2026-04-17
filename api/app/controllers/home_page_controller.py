from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.response_utils import success
from app.db import get_db, SessionLocal
from app.dependencies import get_current_user
from app.models.user_model import User
from app.repositories.home_page_repository import HomePageRepository
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
    """获取系统版本号 + 说明"""
    current_version = None
    version_info = None
    
    # 1️⃣ 优先从数据库获取最新已发布的版本
    try:
        db = SessionLocal()
        try:
            current_version, version_info = HomePageRepository.get_latest_version_introduction(db)
        finally:
            db.close()
    except Exception as e:
        pass
    
    # 2️⃣ 降级：使用环境变量中的版本号
    if not current_version:
        current_version = settings.SYSTEM_VERSION
        version_info = HomePageService.load_version_introduction(current_version)
    
    # 3️⃣ 如果数据库和 JSON 都没有，返回基本信息
    if not version_info:
        version_info = {
            "introduction": {"codeName": "", "releaseDate": "", "upgradePosition": "", "coreUpgrades": []},
            "introduction_en": {"codeName": "", "releaseDate": "", "upgradePosition": "", "coreUpgrades": []}
        }
    
    return success(
        data={
            "version": current_version,
            "introduction": version_info.get("introduction"),
            "introduction_en": version_info.get("introduction_en")
        },
        msg="系统版本获取成功"
    )