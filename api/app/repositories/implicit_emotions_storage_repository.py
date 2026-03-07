"""
Implicit Emotions Storage Repository

数据访问层：处理隐性记忆和情绪数据的数据库操作
事务由调用方控制，仓储层只使用 flush/refresh
"""
import logging
from datetime import datetime, date, timezone, timedelta
from typing import Optional, Generator
from sqlalchemy.orm import Session
from sqlalchemy import select, not_, exists

from app.models.implicit_emotions_storage_model import ImplicitEmotionsStorage
from app.models.end_user_model import EndUser

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

    def get_users_needing_refresh(self, redis_client, batch_size: int = 100) -> Generator[str, None, None]:
        """分批次获取需要刷新隐性记忆/情绪数据的存量用户ID。

        筛选逻辑：
        - 查询 implicit_emotions_storage 中所有用户的 end_user_id 和 updated_at
        - 从 Redis 读取 write_message:last_done:{end_user_id} 的时间戳
        - 若 Redis 中无记录（该用户从未写入过记忆），跳过
        - 若 last_done > updated_at，说明上次刷新后又有新记忆写入，需要刷新
        - 若 last_done <= updated_at，说明已是最新，跳过

        Args:
            redis_client: 同步 redis.StrictRedis 实例（连接 CELERY_BACKEND DB）
            batch_size: 每批次加载的数量

        Yields:
            需要刷新的用户ID字符串
        """
        from datetime import timezone
        offset = 0
        while True:
            try:
                stmt = (
                    select(ImplicitEmotionsStorage.end_user_id, ImplicitEmotionsStorage.updated_at)
                    .order_by(ImplicitEmotionsStorage.end_user_id)
                    .limit(batch_size)
                    .offset(offset)
                )
                batch = self.db.execute(stmt).all()
                if not batch:
                    break

                for end_user_id, updated_at in batch:
                    raw = redis_client.get(f"write_message:last_done:{end_user_id}")
                    if raw is None:
                        # 该用户从未有过 write_message 成功记录，跳过
                        continue
                    try:
                        last_done = datetime.fromisoformat(raw)
                        # 统一去掉时区信息做 naive 比较
                        if last_done.tzinfo is not None:
                            last_done = last_done.astimezone(timezone.utc).replace(tzinfo=None)
                        if updated_at is None or last_done > updated_at:
                            yield end_user_id
                    except Exception as e:
                        logger.warning(f"解析 last_done 时间戳失败: end_user_id={end_user_id}, raw={raw}, error={e}")

                offset += batch_size
            except Exception as e:
                logger.error(f"get_users_needing_refresh 分批查询失败: offset={offset}, error={e}")
                break

    def get_new_user_ids_today(self, batch_size: int = 100) -> Generator[str, None, None]:
        """分批次获取当天新增的、尚未初始化隐性记忆和情绪建议数据的用户ID

        查询逻辑：end_users 表中 created_at 为今天，且在 implicit_emotions_storage 中没有对应记录。
        没有对应记录意味着隐性记忆画像和情绪建议均未初始化，需要对这批用户执行首次初始化。
        end_users.id（UUID）转为字符串后与 implicit_emotions_storage.end_user_id（String）对比。

        Args:
            batch_size: 每批次加载的数量，默认100

        Yields:
            用户ID字符串
        """
        from sqlalchemy import cast, String as SAString
        CST = timezone(timedelta(hours=8))
        now_cst = datetime.now(CST)
        today_start = now_cst.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).replace(tzinfo=None)
        tomorrow_start = today_start + timedelta(days=1)
        offset = 0
        while True:
            try:
                stmt = (
                    select(EndUser.id)
                    .where(
                        EndUser.created_at >= today_start,
                        EndUser.created_at < tomorrow_start,
                        not_(
                            exists(
                                select(ImplicitEmotionsStorage.end_user_id).where(
                                    ImplicitEmotionsStorage.end_user_id == cast(EndUser.id, SAString)
                                )
                            )
                        )
                    )
                    .order_by(EndUser.id)
                    .limit(batch_size)
                    .offset(offset)
                )
                batch = self.db.execute(stmt).scalars().all()
                if not batch:
                    break
                yield from (str(uid) for uid in batch)
                offset += batch_size
            except Exception as e:
                logger.error(f"分批获取当天新增用户ID失败: offset={offset}, error={e}")
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
