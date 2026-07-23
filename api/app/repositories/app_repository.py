import uuid
from datetime import datetime
from typing import List

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.logging_config import get_db_logger
from app.models.app_model import App

# 获取数据库专用日志器
db_logger = get_db_logger()


class AppRepository:
    def __init__(self, db: Session | AsyncSession):
        self.db = db

    def get_apps_by_workspace_id(self, workspace_id: uuid.UUID) -> list[App]:
        """根据工作空间ID查询应用（仅返回未删除的应用）"""
        try:
            apps = (
                self.db.query(App)
                .filter(App.workspace_id == workspace_id, App.is_active.is_(True))
                .all()
            )
            db_logger.info(f"成功查询工作空间 {workspace_id} 下的 {len(apps)} 个应用")
            return apps
        except Exception as e:
            db_logger.error(f"查询工作空间 {workspace_id} 下应用时出错: {str(e)}")
            raise

    def get_apps_by_id(self, app_id: uuid.UUID) -> App:
        try:
            app = self.db.query(App).filter(App.id == app_id, App.is_active.is_(True)).first()
            return app
        except Exception as e:
            raise

    def get_apps_by_name(self, app_name: str, app_type: str, workspace_id: uuid.UUID) -> List[App]:
        try:
            stmt = select(App).where(
                App.name == app_name,
                App.workspace_id == workspace_id,
                App.type == app_type,
                App.is_active.is_(True),
            )
            apps = self.db.execute(stmt).scalars().all()
            return list(apps)
        except Exception as e:
            db_logger.error(f"查询名称 {app_name} 应用异常: {str(e)}")
            raise

    async def count_active_by_workspace_async(self, workspace_id: uuid.UUID) -> int:
        """统计工作空间下激活的应用数量"""
        from app.models.app_model import App
        stmt = select(func.count(App.id)).where(
            App.workspace_id == workspace_id,
            App.is_active.is_(True),
        )
        result = await self.db.execute(stmt)
        return result.scalar() or 0

    async def count_active_by_workspace_before_date_async(
        self, workspace_id: uuid.UUID, before_date: datetime
    ) -> int:
        """统计工作空间下在指定日期前创建的激活应用数量"""
        from app.models.app_model import App
        stmt = select(func.count(App.id)).where(
            App.workspace_id == workspace_id,
            App.is_active.is_(True),
            App.created_at < before_date,
        )
        result = await self.db.execute(stmt)
        return result.scalar() or 0

    async def get_active_release_config_async(self, app_id: uuid.UUID) -> dict | None:
        """获取 app_id 最新激活版本的 config（用于 memory.enabled 检查等）"""
        from app.models.app_release_model import AppRelease
        stmt = (
            select(AppRelease.config)
            .where(
                AppRelease.app_id == app_id,
                AppRelease.is_active.is_(True),
            )
            .order_by(AppRelease.version.desc())
            .limit(1)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def count_active_shares_by_target_workspace_async(self, workspace_id: uuid.UUID) -> int:
        """统计分享到目标工作空间的活跃分享数量"""
        from app.models.appshare_model import AppShare
        stmt = select(func.count(AppShare.id)).where(
            AppShare.target_workspace_id == workspace_id,
            AppShare.is_active.is_(True),
        )
        result = await self.db.execute(stmt)
        return result.scalar() or 0

    async def count_active_shares_by_target_workspace_before_date_async(
        self, workspace_id: uuid.UUID, before_date: datetime
    ) -> int:
        """统计在指定日期前分享到目标工作空间的活跃分享数量"""
        from app.models.appshare_model import AppShare
        stmt = select(func.count(AppShare.id)).where(
            AppShare.target_workspace_id == workspace_id,
            AppShare.is_active.is_(True),
            AppShare.created_at < before_date,
        )
        result = await self.db.execute(stmt)
        return result.scalar() or 0


def get_apps_by_workspace_id(db: Session, workspace_id: uuid.UUID) -> List[App]:
    """根据工作空间ID查询应用"""
    repo = AppRepository(db)
    return repo.get_apps_by_workspace_id(workspace_id)


def get_apps_by_id(db: Session, app_id: uuid.UUID) -> App:
    """根据工作空间ID查询应用"""
    repo = AppRepository(db)
    return repo.get_apps_by_id(app_id)


def get_release_by_id(db: Session, app_id: uuid.UUID, release_id: uuid.UUID):
    """根据发布版本ID查询发布快照（仅返回激活状态）"""
    from app.models.app_release_model import AppRelease
    return db.scalars(
        select(AppRelease).where(
            AppRelease.app_id == app_id,
            AppRelease.id == release_id,
            AppRelease.is_active.is_(True),
        )
    ).first()
