from sqlalchemy.orm import Session
from typing import List, Optional
import uuid

from app.models.app_model import App

from app.core.logging_config import get_db_logger

# 获取数据库专用日志器
db_logger = get_db_logger()


class AppRepository:
    def __init__(self, db: Session):
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

def get_apps_by_workspace_id(db: Session, workspace_id: uuid.UUID) -> List[App]:
    """根据工作空间ID查询应用"""
    repo = AppRepository(db)
    return repo.get_apps_by_workspace_id(workspace_id)

def get_apps_by_id(db: Session, app_id: uuid.UUID) -> App:
    """根据工作空间ID查询应用"""
    repo = AppRepository(db)
    return repo.get_apps_by_id(app_id)
