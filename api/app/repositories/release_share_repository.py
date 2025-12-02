import uuid
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import select
from app.models import ReleaseShare


class ReleaseShareRepository:
    """发布版本分享仓储"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def create(self, release_share: ReleaseShare) -> ReleaseShare:
        """创建分享配置"""
        self.db.add(release_share)
        self.db.commit()
        self.db.refresh(release_share)
        return release_share
    
    def get_by_id(self, share_id: uuid.UUID) -> Optional[ReleaseShare]:
        """根据 ID 获取分享配置"""
        return self.db.get(ReleaseShare, share_id)
    
    def get_by_release_id(self, release_id: uuid.UUID) -> Optional[ReleaseShare]:
        """根据发布版本 ID 获取分享配置"""
        stmt = select(ReleaseShare).where(ReleaseShare.release_id == release_id)
        return self.db.scalars(stmt).first()
    
    def get_by_share_token(self, share_token: str) -> Optional[ReleaseShare]:
        """根据分享 token 获取分享配置"""
        stmt = select(ReleaseShare).where(ReleaseShare.share_token == share_token)
        return self.db.scalars(stmt).first()
    
    def update(self, release_share: ReleaseShare) -> ReleaseShare:
        """更新分享配置"""
        self.db.commit()
        self.db.refresh(release_share)
        return release_share
    
    def delete(self, release_share: ReleaseShare) -> None:
        """删除分享配置"""
        self.db.delete(release_share)
        self.db.commit()
    
    def token_exists(self, share_token: str) -> bool:
        """检查 token 是否已存在"""
        stmt = select(ReleaseShare.id).where(ReleaseShare.share_token == share_token)
        return self.db.scalars(stmt).first() is not None
    
    def increment_view_count(self, share_id: uuid.UUID) -> None:
        """增加访问次数（异步更新，不阻塞）"""
        from datetime import datetime
        stmt = select(ReleaseShare).where(ReleaseShare.id == share_id)
        share = self.db.scalars(stmt).first()
        if share:
            share.view_count += 1
            share.last_accessed_at = datetime.now()
            self.db.commit()
