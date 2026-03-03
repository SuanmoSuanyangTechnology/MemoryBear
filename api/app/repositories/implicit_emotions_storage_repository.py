"""
Implicit Emotions Storage Repository

数据访问层：处理隐性记忆和情绪数据的数据库操作
事务由调用方控制，仓储层只使用 flush/refresh
"""
import logging
from datetime import datetime
from typing import Optional, Generator
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models.implicit_emotions_storage_model import ImplicitEmotionsStorage

logger = logging.getLogger(__name__)


class ImplicitEmotionsStorageRepository:
    """隐性记忆和情绪存储仓储类"""

    def __init__(self, db: Session):
        self.db = db

    def get_by_end_user_id(self, end_user_id: str) -> Optional[ImplicitEmotionsStorage]:
        """根据终端用户ID获取存储记录"""
        try:
            stmt = select(ImplicitEmotionsStorage).where(
                ImplicitEmotionsStorage.end_user_id == end_user_id
            )
            return self.db.execute(stmt).scalar_one_or_none()
        except Exception as e:
            logger.error(f"获取用户存储记录失败: end_user_id={end_user_id}, error={e}")
            return None

    def create(self, end_user_id: str) -> ImplicitEmotionsStorage:
        """创建新的存储记录（事务由调用方提交）"""
        storage = ImplicitEmotionsStorage(
            end_user_id=end_user_id,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        self.db.add(storage)
        self.db.flush()
        self.db.refresh(storage)
        logger.info(f"创建用户存储记录成功: end_user_id={end_user_id}")
        return storage

    def update_implicit_profile(
        self,
        end_user_id: str,
        profile_data: dict
    ) -> ImplicitEmotionsStorage:
        """更新隐性记忆画像数据（事务由调用方提交）"""
        storage = self.get_by_end_user_id(end_user_id)
        if storage is None:
            storage = self.create(end_user_id)

        storage.implicit_profile = profile_data
        storage.implicit_generated_at = datetime.utcnow()
        storage.updated_at = datetime.utcnow()

        self.db.flush()
        self.db.refresh(storage)
        logger.info(f"更新隐性记忆画像成功: end_user_id={end_user_id}")
        return storage

    def update_emotion_suggestions(
        self,
        end_user_id: str,
        suggestions_data: dict
    ) -> ImplicitEmotionsStorage:
        """更新情绪建议数据（事务由调用方提交）"""
        storage = self.get_by_end_user_id(end_user_id)
        if storage is None:
            storage = self.create(end_user_id)

        storage.emotion_suggestions = suggestions_data
        storage.emotion_generated_at = datetime.utcnow()
        storage.updated_at = datetime.utcnow()

        self.db.flush()
        self.db.refresh(storage)
        logger.info(f"更新情绪建议成功: end_user_id={end_user_id}")
        return storage

    def get_all_user_ids(self, batch_size: int = 100) -> Generator[str, None, None]:
        """分批次获取所有已存储数据的用户ID（避免大数据量内存溢出）

        Args:
            batch_size: 每批次加载的数量，默认100

        Yields:
            用户ID字符串
        """
        offset = 0
        while True:
            try:
                stmt = (
                    select(ImplicitEmotionsStorage.end_user_id)
                    .order_by(ImplicitEmotionsStorage.end_user_id)
                    .limit(batch_size)
                    .offset(offset)
                )
                batch = self.db.execute(stmt).scalars().all()
                if not batch:
                    break
                yield from batch
                offset += batch_size
            except Exception as e:
                logger.error(f"分批获取用户ID失败: offset={offset}, error={e}")
                break

    def delete_by_end_user_id(self, end_user_id: str) -> bool:
        """删除用户的存储记录（事务由调用方提交）"""
        storage = self.get_by_end_user_id(end_user_id)
        if storage:
            self.db.delete(storage)
            self.db.flush()
            logger.info(f"删除用户存储记录成功: end_user_id={end_user_id}")
            return True
        return False
