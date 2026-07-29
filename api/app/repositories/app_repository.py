import uuid
from typing import List

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging_config import get_db_logger
from app.models.app_model import App

# 获取数据库专用日志器
db_logger = get_db_logger()


class AppRepository:
    def __init__(self, db: Session):
        self.db = db

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
