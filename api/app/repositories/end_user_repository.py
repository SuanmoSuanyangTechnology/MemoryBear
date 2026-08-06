import uuid
from contextlib import contextmanager
from datetime import datetime
from typing import List, NamedTuple, Optional, Set, TypedDict

import sqlalchemy as sa
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.logging_config import get_db_logger
from app.core.utils.datetime_utils import utcnow_naive
from app.models import User
from app.models.end_user_info_model import EndUserInfo
from app.models.end_user_model import EndUser
from app.models.workspace_model import Workspace
from app.utils.redis_cache import redis_cache

# 获取数据库专用日志器
db_logger = get_db_logger()


class UserTagRefreshCandidate(NamedTuple):
    """扫描阶段使用的轻量候选记录，避免批量加载完整 metadata。"""

    end_user_id: uuid.UUID
    workspace_id: uuid.UUID
    metadata_updated_at: datetime | None


class MemoryCacheRefreshFields(NamedTuple):
    """洞察/摘要 scan 使用的原始字段。"""

    end_user_id: uuid.UUID
    workspace_id: uuid.UUID
    write_time: datetime | None
    metadata_updated_at: datetime | None
    memory_insight_updated_at: datetime | None
    user_summary_updated_at: datetime | None


class MemoryInsightSourceRow(TypedDict):
    meta_data: object
    metadata_row_exists: bool
    metadata_updated_at: datetime | None
    write_time: datetime | None
    memory_insight_updated_at: datetime | None


def is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except (ValueError, TypeError):
        return False


class EndUserRepository:
    def __init__(self, db: Session | AsyncSession):
        self.db = db

    @contextmanager
    def _acquire_eu_lock(self, workspace_id, other_id):
        """获取 EndUser 创建/查找的排他锁，防止并发重复创建。

        使用 pg_advisory_xact_lock，锁在事务提交/回滚时自动释放，
        无需显式 unlock。
        """
        self.db.execute(
            sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"{workspace_id}|{other_id}"},
        )
        yield

    def get_end_users_by_app_id(self, app_id: uuid.UUID) -> List[EndUser]:
        """根据应用ID查询宿主"""
        try:
            end_users = (
                self.db.query(EndUser)
                .filter(EndUser.app_id == app_id, EndUser.is_active == True)
                .all()
            )
            db_logger.info(f"成功查询应用 {app_id} 下的 {len(end_users)} 个宿主")
            return end_users
        except Exception as e:
            self.db.rollback()
            db_logger.error(f"查询应用 {app_id} 下宿主时出错: {str(e)}")
            raise

    def get_end_users_by_workspace(self, workspace_id: uuid.UUID) -> List[EndUser]:
        """获取指定 workspace 下的所有 end_user"""
        try:
            end_users = (
                self.db.query(EndUser)
                .filter(EndUser.workspace_id == workspace_id, EndUser.is_active == True)
                .all()
            )
            db_logger.info(f"成功查询工作空间 {workspace_id} 下的 {len(end_users)} 个终端用户")
            return end_users
        except Exception as e:
            self.db.rollback()
            db_logger.error(f"查询工作空间 {workspace_id} 下终端用户时出错: {str(e)}")
            raise

    async def get_end_users_by_workspace_async(
        self, workspace_id: uuid.UUID
    ) -> List[EndUser]:
        """获取指定 workspace 下的所有活跃 end_user（异步版本）
        返回结果按 created_at 从新到旧排序（NULL 值排在最后）
        """
        from sqlalchemy import desc, nullslast
        try:
            result = await self.db.execute(
                select(EndUser)
                .filter(EndUser.workspace_id == workspace_id, EndUser.is_active == True)
                .order_by(
                    nullslast(desc(EndUser.created_at)),
                    desc(EndUser.id),
                )
            )
            end_users = list(result.scalars().all())
            db_logger.info(f"成功查询工作空间 {workspace_id} 下的 {len(end_users)} 个终端用户")
            return end_users
        except Exception as e:
            await self.db.rollback()
            db_logger.error(f"查询工作空间 {workspace_id} 下终端用户时出错: {str(e)}")
            raise

    def get_end_users_count_by_workspace(self, workspace_id: uuid.UUID) -> int:
        """获取指定 workspace 下的所有 end_user数量"""
        try:
            end_users_count = (
                self.db.query(EndUser)
                .filter(EndUser.workspace_id == workspace_id, EndUser.is_active == True)
                .count()
            )
            db_logger.info(f"成功查询工作空间 {workspace_id} 下的 {end_users_count} 个终端用户")
            return end_users_count
        except Exception as e:
            self.db.rollback()
            db_logger.error(f"查询工作空间 {workspace_id} 下终端用户时出错: {str(e)}")
            raise

    def get_end_user_by_id(self, end_user_id: uuid.UUID) -> Optional[EndUser]:
        """根据 end_user_id 查询宿主"""
        try:
            end_user = (
                self.db.query(EndUser)
                .filter(EndUser.id == end_user_id, EndUser.is_active == True)
                .first()
            )
            if end_user:
                db_logger.info(f"成功查询到宿主 {end_user_id}")
            else:
                db_logger.info(f"未找到宿主 {end_user_id}")
            return end_user
        except Exception as e:
            self.db.rollback()
            db_logger.error(f"查询宿主 {end_user_id} 时出错: {str(e)}")
            raise

    def get_active_end_user_in_workspace(
        self,
        end_user_id: uuid.UUID,
        workspace_id: uuid.UUID,
    ) -> Optional[EndUser]:
        """查询当前工作空间内的有效终端用户。"""
        try:
            return (
                self.db.query(EndUser)
                .filter(
                    EndUser.id == end_user_id,
                    EndUser.workspace_id == workspace_id,
                    EndUser.is_active.is_(True),
                )
                .first()
            )
        except Exception as e:
            self.db.rollback()
            db_logger.error(
                f"查询工作空间 {workspace_id} 下的终端用户 "
                f"{end_user_id} 时出错: {str(e)}"
            )
            raise

    async def get_end_user_by_id_async(self, end_user_id: uuid.UUID) -> Optional[EndUser]:
        try:
            result = await self.db.execute(
                select(EndUser).filter(EndUser.id == end_user_id, EndUser.is_active == True)
            )
            end_user = result.scalars().first()
            if end_user:
                db_logger.info(f"成功查询到宿主 {end_user_id}")
            else:
                db_logger.info(f"未找到宿主 {end_user_id}")
            return end_user
        except Exception as e:
            await self.db.rollback()
            db_logger.error(f"查询宿主 {end_user_id} 时出错: {str(e)}")
            raise

    def get_end_user_by_other_id(self, workspace_id: uuid.UUID, other_id: str) -> Optional["EndUser"]:
        """按 workspace_id + other_id 查找终端用户，不存在返回 None"""
        return (
            self.db.query(EndUser)
            .filter(
                EndUser.workspace_id == workspace_id,
                EndUser.other_id == other_id,
                EndUser.is_active == True,
            ).order_by(EndUser.created_at.asc())
            .first()
        )

    def get_or_create_end_user(
            self,
            app_id: uuid.UUID,
            workspace_id: uuid.UUID,
            other_id: str,
            original_user_id: Optional[str] = None,
            other_name: Optional[str] = None
    ) -> EndUser:
        """获取或创建终端用户
        
        Args:
            app_id: 应用ID
            workspace_id: 工作空间ID
            other_id: 第三方ID
            original_user_id: 原始用户ID (存储到 other_id)
            other_name: 用户名称（用于创建 EndUserInfo）
        """
        try:
            with self._acquire_eu_lock(workspace_id, other_id):
                # 尝试查找现有用户
                end_user = (
                    self.db.query(EndUser)
                    .filter(
                        EndUser.workspace_id == workspace_id,
                        EndUser.other_id == other_id,
                        EndUser.is_active == True,
                    )
                    .order_by(EndUser.created_at.asc())
                    .first()
                )

                if end_user:
                    db_logger.debug(f"找到现有终端用户: 应用ID {workspace_id}、第三方ID {other_id}")
                    end_user.app_id = app_id
                    self.db.commit()
                    self.db.refresh(end_user)
                    return end_user

                # 创建新用户
                end_user = EndUser(
                    app_id=app_id,
                    workspace_id=workspace_id,
                    other_id=other_id
                )
                self.db.add(end_user)
                self.db.flush()  # 刷新以获取 end_user.id，但不提交事务

                # 创建对应的 EndUserInfo 记录
                end_user_info = EndUserInfo(
                    end_user_id=end_user.id,
                    other_name=other_name or "",  # 如果没有提供 other_name，使用空字符串
                    aliases=[],
                    meta_data={}
                )
                self.db.add(end_user_info)

                # 一起提交
                self.db.commit()
            self.db.refresh(end_user)

            db_logger.info(f"创建新终端用户及其信息: (other_id: {other_id}) for workspace {workspace_id}")
            return end_user

        except Exception as e:
            self.db.rollback()
            db_logger.error(f"获取或创建终端用户时出错: {str(e)}")
            raise

    async def get_end_user_by_other_id_async(self, workspace_id: uuid.UUID, other_id: str) -> Optional["EndUser"]:
        result = await self.db.execute(
            select(EndUser)
            .filter(
                EndUser.workspace_id == workspace_id,
                EndUser.other_id == other_id,
                EndUser.is_active == True,
            )
            .order_by(EndUser.created_at.asc())
        )
        return result.scalars().first()

    async def get_or_create_end_user_async(
            self,
            app_id: uuid.UUID,
            workspace_id: uuid.UUID,
            other_id: str,
            original_user_id: Optional[str] = None,
            other_name: Optional[str] = None
    ) -> EndUser:
        try:
            end_user = await self.get_end_user_by_other_id_async(workspace_id, other_id)
            if end_user:
                db_logger.debug(f"找到现有终端用户: 应用ID {workspace_id}、第三方ID {other_id}")
                end_user.app_id = app_id
                await self.db.commit()
                await self.db.refresh(end_user)
                return end_user

            end_user = EndUser(
                app_id=app_id,
                workspace_id=workspace_id,
                other_id=other_id
            )
            self.db.add(end_user)
            await self.db.flush()

            end_user_info = EndUserInfo(
                end_user_id=end_user.id,
                other_name=other_name or "",
                aliases=[],
                meta_data={}
            )
            self.db.add(end_user_info)

            await self.db.commit()
            await self.db.refresh(end_user)

            db_logger.info(f"创建新终端用户及其信息: (other_id: {other_id}) for workspace {workspace_id}")
            return end_user
        except Exception as e:
            await self.db.rollback()
            db_logger.error(f"获取或创建终端用户时出错: {str(e)}")
            raise

    def get_or_create_end_user_mcp(
            self,
            workspace_id: uuid.UUID,
            user_id: str
    ):
        try:
            with self._acquire_eu_lock(workspace_id, user_id):
                if is_uuid(user_id):
                    user_stmt = select(User.id).where(
                        User.is_active == True,
                        User.id == user_id
                    )
                    if self.db.scalar(user_stmt):
                        raise Exception("不可对草稿运行用户创建mcp user")

                    end_user_stmt = select(EndUser).where(
                        EndUser.is_active == True,
                        EndUser.workspace_id == workspace_id,
                        or_(
                            EndUser.id == user_id,
                            EndUser.other_id == user_id,
                        )
                    ).order_by(EndUser.created_at.asc()).limit(1)
                else:
                    end_user_stmt = select(EndUser).where(
                        EndUser.is_active == True,
                        EndUser.workspace_id == workspace_id,
                        EndUser.other_id == user_id,
                    ).order_by(EndUser.created_at.asc()).limit(1)

                existing: EndUser | None = self.db.scalar(end_user_stmt)
                if existing:
                    return existing.id, existing.other_id

                end_user = EndUser(
                    workspace_id=workspace_id,
                    other_id=user_id
                )
                self.db.add(end_user)
                self.db.flush()  # flush to get end_user.id before creating EndUserInfo

                # Create corresponding EndUserInfo record
                end_user_info = EndUserInfo(
                    end_user_id=end_user.id,
                    other_name="",
                    aliases=[],
                    meta_data={}
                )
                self.db.add(end_user_info)

                self.db.commit()
                self.db.refresh(end_user)
                return end_user.id, end_user.other_id

        except Exception as e:
            self.db.rollback()
            db_logger.error(f"获取或创建终端用户出错: {str(e)}")
            raise

    def get_or_create_end_user_with_config(
            self,
            app_id: Optional[uuid.UUID],
            workspace_id: uuid.UUID,
            other_id: str,
            memory_config_id: Optional[uuid.UUID] = None,
            other_name: Optional[str] = None
    ) -> EndUser:
        """获取或创建终端用户，并在单次事务中关联记忆配置。
        
        与 get_or_create_end_user 类似，但额外支持在创建/获取时
        一并设置 memory_config_id，避免多次提交。
        
        Args:
            app_id: 应用ID（可为 None）
            workspace_id: 工作空间ID
            other_id: 第三方ID
            memory_config_id: 记忆配置ID（可选，仅在用户尚无配置时设置）
            other_name: 用户名称（用于创建 EndUserInfo）
            
        Returns:
            EndUser: 终端用户对象（已关联记忆配置）
        """
        try:
            end_user = (
                self.db.query(EndUser)
                .filter(
                    EndUser.workspace_id == workspace_id,
                    EndUser.other_id == other_id,
                    EndUser.is_active == True,
                )
                .order_by(EndUser.created_at.asc())
                .first()
            )

            if end_user:
                db_logger.debug(f"找到现有终端用户: workspace_id={workspace_id}, other_id={other_id}")
                if app_id is not None:
                    end_user.app_id = app_id
                if memory_config_id and not end_user.memory_config_id:
                    end_user.memory_config_id = memory_config_id
                self.db.commit()
                self.db.refresh(end_user)
                return end_user

            # 创建新用户（is_active 默认为 True）
            end_user = EndUser(
                app_id=app_id,
                workspace_id=workspace_id,
                other_id=other_id,
                memory_config_id=memory_config_id,
            )
            self.db.add(end_user)
            self.db.flush()

            end_user_info = EndUserInfo(
                end_user_id=end_user.id,
                other_name=other_name or "",
                aliases=[],
                meta_data={}
            )
            self.db.add(end_user_info)

            self.db.commit()
            self.db.refresh(end_user)

            db_logger.info(
                f"创建新终端用户及其信息: (other_id: {other_id}) for workspace {workspace_id}, "
                f"memory_config_id={memory_config_id}"
            )
            return end_user

        except Exception as e:
            self.db.rollback()
            db_logger.error(f"获取或创建终端用户(含配置)时出错: {str(e)}")
            raise

    async def get_or_create_end_user_with_config_async(
            self,
            app_id: Optional[uuid.UUID],
            workspace_id: uuid.UUID,
            other_id: str,
            memory_config_id: Optional[uuid.UUID] = None,
            other_name: Optional[str] = None
    ) -> EndUser:
        """异步版：获取或创建终端用户，并关联记忆配置。"""
        from sqlalchemy import select

        try:
            stmt = (
                select(EndUser)
                .where(
                    EndUser.workspace_id == workspace_id,
                    EndUser.other_id == other_id,
                    EndUser.is_active == True,
                )
                .order_by(EndUser.created_at.asc())
                .limit(1)
            )
            result = await self.db.execute(stmt)
            end_user = result.scalars().first()

            if end_user:
                db_logger.debug(f"找到现有终端用户(async): workspace_id={workspace_id}, other_id={other_id}")
                if app_id is not None:
                    end_user.app_id = app_id
                if memory_config_id and not end_user.memory_config_id:
                    end_user.memory_config_id = memory_config_id
                await self.db.commit()
                await self.db.refresh(end_user)
                return end_user

            # 创建新用户
            end_user = EndUser(
                app_id=app_id,
                workspace_id=workspace_id,
                other_id=other_id,
                memory_config_id=memory_config_id,
            )
            self.db.add(end_user)
            await self.db.flush()

            end_user_info = EndUserInfo(
                end_user_id=end_user.id,
                other_name=other_name or "",
                aliases=[],
                meta_data={}
            )
            self.db.add(end_user_info)

            await self.db.commit()
            await self.db.refresh(end_user)

            db_logger.info(
                f"创建新终端用户及其信息(async): (other_id: {other_id}) for workspace {workspace_id}, "
                f"memory_config_id={memory_config_id}"
            )
            return end_user

        except Exception as e:
            await self.db.rollback()
            db_logger.error(f"获取或创建终端用户(含配置/async)时出错: {str(e)}")
            raise

    def get_by_id(self, end_user_id: uuid.UUID) -> Optional[EndUser]:
        """根据ID获取终端用户（用于缓存操作）
        
        Args:
            end_user_id: 终端用户ID
            
        Returns:
            Optional[EndUser]: 终端用户对象，如果不存在则返回None
        """
        try:
            end_user = (
                self.db.query(EndUser)
                .filter(EndUser.id == end_user_id, EndUser.is_active == True)
                .first()
            )
            if end_user:
                db_logger.debug(f"成功查询到终端用户 {end_user_id}")
            else:
                db_logger.debug(f"未找到终端用户 {end_user_id}")
            return end_user
        except Exception as e:
            self.db.rollback()
            db_logger.error(f"查询终端用户 {end_user_id} 时出错: {str(e)}")
            raise

    async def get_by_id_async(self, end_user_id: uuid.UUID) -> Optional[EndUser]:
        """根据ID获取终端用户（异步版本，供 AsyncSession 调用方使用）。"""
        try:
            result = await self.db.execute(
                select(EndUser).where(
                    EndUser.id == end_user_id,
                    EndUser.is_active == True,
                )
            )
            end_user = result.scalars().first()
            if end_user:
                db_logger.debug(f"成功查询到终端用户 {end_user_id}(异步)")
            else:
                db_logger.debug(f"未找到终端用户 {end_user_id}(异步)")
            return end_user
        except Exception as e:
            await self.db.rollback()
            db_logger.error(f"查询终端用户 {end_user_id} 时出错(异步): {str(e)}")
            raise

    def filter_existing_ids(self, end_user_ids: List[uuid.UUID]) -> Set[str]:
        """批量校验 end_user_id 是否存在，返回实际存在且活跃的 ID 集合。

        Args:
            end_user_ids: 待校验的终端用户 ID 列表

        Returns:
            set[str]: 存在且 is_active=True 的 end_user_id 字符串集合
        """
        if not end_user_ids:
            return set()
        try:
            rows = (
                self.db.query(EndUser.id)
                .filter(EndUser.id.in_(end_user_ids), EndUser.is_active == True)
                .all()
            )
            return {str(uid) for (uid,) in rows}
        except Exception as e:
            self.db.rollback()
            db_logger.error(f"批量校验终端用户ID时出错: {str(e)}")
            raise

    async def get_memory_insight_by_end_user_id_async(
            self,
            end_user_id: uuid.UUID,
            workspace_id: uuid.UUID,
    ) -> Optional[dict]:
        """按 Workspace scope 获取用户缓存的记忆洞察。"""
        result = await self.db.execute(
            select(
                EndUser.memory_insight,
                EndUser.behavior_pattern,
                EndUser.key_findings,
                EndUser.growth_trajectory,
                EndUser.memory_insight_updated_at,
            )
            .select_from(EndUser)
            .join(Workspace, Workspace.id == EndUser.workspace_id)
            .where(
                EndUser.id == end_user_id,
                EndUser.workspace_id == workspace_id,
                EndUser.is_active.is_(True),
                Workspace.is_active.is_(True),
            )
            .limit(1)
        )
        return result.mappings().one_or_none()

    async def get_user_summary_by_end_user_id_async(self, end_user_id: uuid.UUID) -> Optional[dict]:
        """获取用户缓存的用户摘要"""
        from sqlalchemy import select
        result = await self.db.execute(
            select(
                EndUser.user_summary,
                EndUser.personality_traits,
                EndUser.core_values,
                EndUser.one_sentence_summary,
                EndUser.user_summary_updated_at,
                EndUser.memory_tags,
            ).where(EndUser.id == end_user_id).limit(1)
        )
        return result.mappings().one_or_none()

    def get_user_tag_refresh_candidates(
            self,
            after_id: uuid.UUID | None,
            limit: int,
    ) -> List[UserTagRefreshCandidate]:
        """按主键游标分页获取需要刷新名片 Tag 的有效用户。

        Tag 缓存字段不完整，或 metadata 的更新时间晚于 Tag 更新时间时，用户会进入候选集。
        查询只返回派发任务所需的轻量字段，避免扫描阶段占用过多数据库连接和内存。
        """
        query = (
            self.db.query(
                EndUser.id,
                EndUser.workspace_id,
                EndUserInfo.updated_at,
            )
            .join(EndUserInfo, EndUserInfo.end_user_id == EndUser.id)
            .join(Workspace, Workspace.id == EndUser.workspace_id)
            .filter(
                EndUser.is_active.is_(True),
                Workspace.is_active.is_(True),
                or_(
                    EndUser.memory_tags.is_(None),
                    EndUser.memory_tags_source_fingerprint.is_(None),
                    EndUser.memory_tags_updated_at.is_(None),
                    EndUserInfo.updated_at > EndUser.memory_tags_updated_at,
                ),
            )
        )
        if after_id is not None:
            # 使用主键游标继续下一页，避免数据量增大时 OFFSET 扫描逐页变慢。
            query = query.filter(EndUser.id > after_id)

        rows = query.order_by(EndUser.id.asc()).limit(limit).all()
        return [
            UserTagRefreshCandidate(
                end_user_id=row[0],
                workspace_id=row[1],
                metadata_updated_at=row[2],
            )
            for row in rows
        ]

    def get_scoped_user_tag_source(
            self,
            workspace_id: uuid.UUID,
            end_user_id: uuid.UUID,
    ) -> Optional[dict]:
        """读取有效 Workspace 下单个有效用户的 Tag 源数据和当前缓存状态。"""
        result = self.db.execute(
            select(
                EndUserInfo.meta_data.label("meta_data"),
                EndUserInfo.updated_at.label("metadata_updated_at"),
                EndUser.memory_tags.label("memory_tags"),
                EndUser.memory_tags_source_fingerprint.label("memory_tags_source_fingerprint"),
            )
            .select_from(EndUser)
            .join(EndUserInfo, EndUserInfo.end_user_id == EndUser.id)
            .join(Workspace, Workspace.id == EndUser.workspace_id)
            .where(
                EndUser.id == end_user_id,
                EndUser.workspace_id == workspace_id,
                EndUser.is_active.is_(True),
                Workspace.is_active.is_(True),
            )
            .limit(1)
        )
        row = result.mappings().one_or_none()
        if row is None:
            return None
        return {
            "meta_data": row["meta_data"],
            "metadata_updated_at": row["metadata_updated_at"],
            "memory_tags": row["memory_tags"],
            "memory_tags_source_fingerprint": row["memory_tags_source_fingerprint"],
        }

    def update_user_tags_if_source_unchanged(
            self,
            workspace_id: uuid.UUID,
            end_user_id: uuid.UUID,
            expected_metadata_updated_at: datetime | None,
            tags: List[str],
            source_fingerprint: str,
            refreshed_at: datetime,
    ) -> bool:
        """仅在 metadata 仍是读取时的版本时更新 Tag 缓存及源指纹。

        LLM 生成期间 metadata 可能被其他任务修改。把版本条件放进 UPDATE，可避免较旧的
        LLM 结果覆盖新数据；返回 False 表示版本已变化或用户、Workspace 已失效。
        """
        timestamp_matches = (
            EndUserInfo.updated_at.is_(None)
            if expected_metadata_updated_at is None
            else EndUserInfo.updated_at == expected_metadata_updated_at
        )
        source_unchanged = sa.exists().where(
            EndUserInfo.end_user_id == end_user_id,
            timestamp_matches,
        )
        workspace_active = sa.exists().where(
            Workspace.id == workspace_id,
            Workspace.is_active.is_(True),
        )

        try:
            result = self.db.execute(
                sa.update(EndUser)
                .where(
                    EndUser.id == end_user_id,
                    EndUser.workspace_id == workspace_id,
                    EndUser.is_active.is_(True),
                    source_unchanged,
                    workspace_active,
                )
                .values(
                    memory_tags=list(tags),
                    memory_tags_updated_at=refreshed_at,
                    memory_tags_source_fingerprint=source_fingerprint,
                )
                .execution_options(synchronize_session=False)
            )
            self.db.commit()
            return bool(result.rowcount)
        except Exception:
            self.db.rollback()
            raise

    async def get_scoped_memory_insight_source_async(
            self,
            workspace_id: uuid.UUID,
            end_user_id: uuid.UUID,
    ) -> MemoryInsightSourceRow | None:
        """异步读取 Neo4j Workspace 下的洞察源数据和并发版本。"""
        result = await self.db.execute(
            select(
                EndUserInfo.id.label("metadata_id"),
                EndUserInfo.meta_data.label("meta_data"),
                EndUserInfo.updated_at.label("metadata_updated_at"),
                EndUser.write_time.label("write_time"),
                EndUser.memory_insight_updated_at.label("memory_insight_updated_at"),
            )
            .select_from(EndUser)
            .join(Workspace, Workspace.id == EndUser.workspace_id)
            .outerjoin(EndUserInfo, EndUserInfo.end_user_id == EndUser.id)
            .where(
                EndUser.id == end_user_id,
                EndUser.workspace_id == workspace_id,
                EndUser.is_active.is_(True),
                Workspace.is_active.is_(True),
                Workspace.storage_type == "neo4j",
            )
            .limit(1)
        )
        row = result.mappings().one_or_none()
        if row is None:
            return None
        return {
            "meta_data": row["meta_data"],
            "metadata_row_exists": row["metadata_id"] is not None,
            "metadata_updated_at": row["metadata_updated_at"],
            "write_time": row["write_time"],
            "memory_insight_updated_at": row["memory_insight_updated_at"],
        }

    def get_scoped_memory_insight_source(
            self,
            workspace_id: uuid.UUID,
            end_user_id: uuid.UUID,
    ) -> MemoryInsightSourceRow | None:
        """同步读取 Neo4j Workspace 下的洞察源数据和并发版本。"""
        result = self.db.execute(
            select(
                EndUserInfo.id.label("metadata_id"),
                EndUserInfo.meta_data.label("meta_data"),
                EndUserInfo.updated_at.label("metadata_updated_at"),
                EndUser.write_time.label("write_time"),
                EndUser.memory_insight_updated_at.label("memory_insight_updated_at"),
            )
            .select_from(EndUser)
            .join(Workspace, Workspace.id == EndUser.workspace_id)
            .outerjoin(EndUserInfo, EndUserInfo.end_user_id == EndUser.id)
            .where(
                EndUser.id == end_user_id,
                EndUser.workspace_id == workspace_id,
                EndUser.is_active.is_(True),
                Workspace.is_active.is_(True),
                Workspace.storage_type == "neo4j",
            )
            .limit(1)
        )
        row = result.mappings().one_or_none()
        if row is None:
            return None
        return {
            "meta_data": row["meta_data"],
            "metadata_row_exists": row["metadata_id"] is not None,
            "metadata_updated_at": row["metadata_updated_at"],
            "write_time": row["write_time"],
            "memory_insight_updated_at": row["memory_insight_updated_at"],
        }

    def update_grounded_memory_insight_if_source_unchanged(
            self,
            workspace_id: uuid.UUID,
            end_user_id: uuid.UUID,
            expected_write_time: datetime | None,
            expected_metadata_row_exists: bool,
            expected_metadata_updated_at: datetime | None,
            memory_insight: str,
            key_findings: str,
            refreshed_at: datetime,
    ) -> bool:
        """仅在洞察源版本未变化时写回当前缓存。"""
        workspace_active = sa.exists().where(
            Workspace.id == workspace_id,
            Workspace.is_active.is_(True),
            Workspace.storage_type == "neo4j",
        )
        metadata_exists = sa.exists().where(EndUserInfo.end_user_id == end_user_id)
        if expected_metadata_row_exists:
            metadata_unchanged = sa.exists().where(
                EndUserInfo.end_user_id == end_user_id,
                EndUserInfo.updated_at.is_not_distinct_from(expected_metadata_updated_at),
            )
        else:
            metadata_unchanged = ~metadata_exists

        try:
            result = self.db.execute(
                sa.update(EndUser)
                .where(
                    EndUser.id == end_user_id,
                    EndUser.workspace_id == workspace_id,
                    EndUser.is_active.is_(True),
                    EndUser.write_time.is_not_distinct_from(expected_write_time),
                    workspace_active,
                    metadata_unchanged,
                )
                .values(
                    memory_insight=memory_insight,
                    memory_insight_updated_at=refreshed_at,
                    key_findings=key_findings,
                    behavior_pattern="",
                    growth_trajectory="",
                )
                .execution_options(synchronize_session=False)
            )
            self.db.commit()
            return bool(result.rowcount)
        except Exception:
            self.db.rollback()
            raise

    async def update_grounded_memory_insight_if_source_unchanged_async(
            self,
            workspace_id: uuid.UUID,
            end_user_id: uuid.UUID,
            expected_write_time: datetime | None,
            expected_metadata_row_exists: bool,
            expected_metadata_updated_at: datetime | None,
            memory_insight: str,
            key_findings: str,
            refreshed_at: datetime,
    ) -> bool:
        """源数据未变化时异步写回当前洞察缓存。"""
        workspace_active = sa.exists().where(
            Workspace.id == workspace_id,
            Workspace.is_active.is_(True),
            Workspace.storage_type == "neo4j",
        )
        metadata_exists = sa.exists().where(EndUserInfo.end_user_id == end_user_id)
        if expected_metadata_row_exists:
            metadata_unchanged = sa.exists().where(
                EndUserInfo.end_user_id == end_user_id,
                EndUserInfo.updated_at.is_not_distinct_from(expected_metadata_updated_at),
            )
        else:
            metadata_unchanged = ~metadata_exists

        try:
            result = await self.db.execute(
                sa.update(EndUser)
                .where(
                    EndUser.id == end_user_id,
                    EndUser.workspace_id == workspace_id,
                    EndUser.is_active.is_(True),
                    EndUser.write_time.is_not_distinct_from(expected_write_time),
                    workspace_active,
                    metadata_unchanged,
                )
                .values(
                    memory_insight=memory_insight,
                    memory_insight_updated_at=refreshed_at,
                    key_findings=key_findings,
                    behavior_pattern="",
                    growth_trajectory="",
                )
                .execution_options(synchronize_session=False)
            )
            await self.db.commit()
            return bool(result.rowcount)
        except Exception:
            await self.db.rollback()
            raise

    def get_neo4j_memory_cache_refresh_fields(
            self,
            workspace_id: uuid.UUID,
    ) -> List[MemoryCacheRefreshFields]:
        """返回指定 Neo4j Workspace 下的缓存刷新原始字段。"""
        rows = (
            self.db.query(
                EndUser.id,
                EndUser.workspace_id,
                EndUser.write_time,
                EndUserInfo.updated_at,
                EndUser.memory_insight_updated_at,
                EndUser.user_summary_updated_at,
            )
            .join(Workspace, Workspace.id == EndUser.workspace_id)
            .outerjoin(EndUserInfo, EndUserInfo.end_user_id == EndUser.id)
            .filter(
                EndUser.workspace_id == workspace_id,
                EndUser.is_active.is_(True),
                Workspace.is_active.is_(True),
                Workspace.storage_type == "neo4j",
            )
            .all()
        )
        return [MemoryCacheRefreshFields(*row) for row in rows]

    async def get_forgetting_threshold_async(self, end_user_id: uuid.UUID) -> Optional[float]:
        """获取用户的遗忘阈值配置。

        路径: EndUser → workspace → workspace.memory_config → MemoryConfig.forgetting_threshold
        """
        from sqlalchemy import select
        from app.models.memory_config_model import MemoryConfig
        from app.models.workspace_model import Workspace

        stmt = (
            select(MemoryConfig.forgetting_threshold)
            .select_from(EndUser)
            .join(Workspace, Workspace.id == EndUser.workspace_id)
            .join(MemoryConfig, MemoryConfig.config_id == Workspace.memory_config)
            .where(
                EndUser.id == end_user_id,
                Workspace.is_active.is_(True),
            )
            .limit(1)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    def update_memory_insight(
            self,
            end_user_id: uuid.UUID,
            memory_insight: str,
            behavior_pattern: str,
            key_findings: str,
            growth_trajectory: str
    ) -> bool:
        """更新记忆洞察缓存（四个维度）
        
        Args:
            end_user_id: 终端用户ID
            memory_insight: 总体概述
            behavior_pattern: 行为模式
            key_findings: 关键发现
            growth_trajectory: 成长轨迹
            
        Returns:
            bool: 更新成功返回True，否则返回False
        """
        try:
            updated_count = (
                self.db.query(EndUser)
                .filter(EndUser.id == end_user_id, EndUser.is_active == True)
                .update(
                    {
                        EndUser.memory_insight: memory_insight,  # 总体概述存储在 memory_insight
                        EndUser.behavior_pattern: behavior_pattern,
                        EndUser.key_findings: key_findings,
                        EndUser.growth_trajectory: growth_trajectory,
                        EndUser.memory_insight_updated_at: utcnow_naive()
                    },
                    synchronize_session=False
                )
            )

            self.db.commit()

            if updated_count > 0:
                db_logger.info(f"成功更新终端用户 {end_user_id} 的记忆洞察缓存（四维度）")
                return True
            else:
                db_logger.warning(f"未找到终端用户 {end_user_id}，无法更新记忆洞察缓存")
                return False

        except Exception as e:
            self.db.rollback()
            db_logger.error(f"更新终端用户 {end_user_id} 的记忆洞察缓存时出错: {str(e)}")
            raise

    async def update_memory_insight_async(
            self,
            end_user_id: uuid.UUID,
            memory_insight: str,
            behavior_pattern: str,
            key_findings: str,
            growth_trajectory: str,
    ) -> bool:
        """更新记忆洞察缓存（四个维度，异步版本）。"""
        try:
            result = await self.db.execute(
                update(EndUser)
                .where(EndUser.id == end_user_id, EndUser.is_active == True)
                .values(
                    memory_insight=memory_insight,
                    behavior_pattern=behavior_pattern,
                    key_findings=key_findings,
                    growth_trajectory=growth_trajectory,
                    memory_insight_updated_at=utcnow_naive(),
                )
                .execution_options(synchronize_session=False)
            )
            await self.db.commit()

            if result.rowcount > 0:
                db_logger.info(f"成功更新终端用户 {end_user_id} 的记忆洞察缓存(异步)")
                return True
            db_logger.warning(f"未找到终端用户 {end_user_id}，无法更新记忆洞察缓存(异步)")
            return False
        except Exception as e:
            await self.db.rollback()
            db_logger.error(f"更新终端用户 {end_user_id} 的记忆洞察缓存时出错(异步): {str(e)}")
            raise

    def update_user_summary(
            self,
            end_user_id: uuid.UUID,
            user_summary: str,
            personality: str,
            core_values: str,
            one_sentence: str
    ) -> bool:
        """更新用户摘要缓存（四个部分）
        
        Args:
            end_user_id: 终端用户ID
            user_summary: 基本介绍
            personality: 性格特点
            core_values: 核心价值观
            one_sentence: 一句话总结
            
        Returns:
            bool: 更新成功返回True，否则返回False
        """
        try:
            updated_count = (
                self.db.query(EndUser)
                .filter(EndUser.id == end_user_id, EndUser.is_active == True)
                .update(
                    {
                        EndUser.user_summary: user_summary,  # 基本介绍存储在 user_summary
                        EndUser.personality_traits: personality,
                        EndUser.core_values: core_values,
                        EndUser.one_sentence_summary: one_sentence,
                        EndUser.user_summary_updated_at: utcnow_naive()
                    },
                    synchronize_session=False
                )
            )

            self.db.commit()

            if updated_count > 0:
                db_logger.info(f"成功更新终端用户 {end_user_id} 的用户摘要缓存（四部分）")
                return True
            else:
                db_logger.warning(f"未找到终端用户 {end_user_id}，无法更新用户摘要缓存")
                return False

        except Exception as e:
            self.db.rollback()
            db_logger.error(f"更新终端用户 {end_user_id} 的用户摘要缓存时出错: {str(e)}")
            raise

    async def update_user_summary_async(
            self,
            end_user_id: uuid.UUID,
            user_summary: str,
            personality: str,
            core_values: str,
            one_sentence: str,
    ) -> bool:
        """更新用户摘要缓存（四个部分，异步版本）。"""
        try:
            result = await self.db.execute(
                update(EndUser)
                .where(EndUser.id == end_user_id, EndUser.is_active == True)
                .values(
                    user_summary=user_summary,
                    personality_traits=personality,
                    core_values=core_values,
                    one_sentence_summary=one_sentence,
                    user_summary_updated_at=utcnow_naive(),
                )
                .execution_options(synchronize_session=False)
            )
            await self.db.commit()

            if result.rowcount > 0:
                db_logger.info(f"成功更新终端用户 {end_user_id} 的用户摘要缓存(异步)")
                return True
            db_logger.warning(f"未找到终端用户 {end_user_id}，无法更新用户摘要缓存(异步)")
            return False
        except Exception as e:
            await self.db.rollback()
            db_logger.error(f"更新终端用户 {end_user_id} 的用户摘要缓存时出错(异步): {str(e)}")
            raise

    def update_rag_summary_tags(
            self,
            end_user_id: uuid.UUID,
            user_summary: str,
            rag_tags: str,
            rag_personas: str,
    ) -> bool:
        """更新RAG模式下的用户摘要、标签和人物形象缓存
        
        Args:
            end_user_id: 终端用户ID
            user_summary: 用户摘要文本
            rag_tags: 标签列表（JSON字符串）
            rag_personas: 人物形象列表（JSON字符串）
            
        Returns:
            bool: 更新成功返回True，否则返回False
        """
        try:
            updated_count = (
                self.db.query(EndUser)
                .filter(EndUser.id == end_user_id, EndUser.is_active == True)
                .update(
                    {
                        EndUser.user_summary: user_summary,
                        EndUser.rag_tags: rag_tags,
                        EndUser.rag_personas: rag_personas,
                        EndUser.rag_summary_updated_at: utcnow_naive(),
                    },
                    synchronize_session=False
                )
            )
            self.db.commit()
            if updated_count > 0:
                db_logger.info(f"成功更新终端用户 {end_user_id} 的RAG摘要/标签/人物形象缓存")
                return True
            else:
                db_logger.warning(f"未找到终端用户 {end_user_id}，无法更新RAG摘要缓存")
                return False
        except Exception as e:
            self.db.rollback()
            db_logger.error(f"更新终端用户 {end_user_id} 的RAG摘要缓存时出错: {str(e)}")
            raise

    def update_rag_insight(
            self,
            end_user_id: uuid.UUID,
            memory_insight: str,
    ) -> bool:
        """更新RAG模式下的记忆洞察缓存
        
        Args:
            end_user_id: 终端用户ID
            memory_insight: 洞察文本
            
        Returns:
            bool: 更新成功返回True，否则返回False
        """
        try:
            updated_count = (
                self.db.query(EndUser)
                .filter(EndUser.id == end_user_id, EndUser.is_active == True)
                .update(
                    {
                        EndUser.memory_insight: memory_insight,
                        EndUser.memory_insight_updated_at: utcnow_naive(),
                    },
                    synchronize_session=False
                )
            )
            self.db.commit()
            if updated_count > 0:
                db_logger.info(f"成功更新终端用户 {end_user_id} 的RAG洞察缓存")
                return True
            else:
                db_logger.warning(f"未找到终端用户 {end_user_id}，无法更新RAG洞察缓存")
                return False
        except Exception as e:
            self.db.rollback()
            db_logger.error(f"更新终端用户 {end_user_id} 的RAG洞察缓存时出错: {str(e)}")
            raise

    def get_all_by_workspace(self, workspace_id: uuid.UUID) -> List[EndUser]:
        """获取工作空间的所有终端用户
        
        Args:
            workspace_id: 工作空间ID
            
        Returns:
            List[EndUser]: 终端用户列表
        """
        try:
            end_users = (
                self.db.query(EndUser)
                .filter(EndUser.workspace_id == workspace_id, EndUser.is_active == True)
                .all()
            )
            db_logger.debug(f"成功查询工作空间 {workspace_id} 下的 {len(end_users)} 个终端用户")
            return end_users
        except Exception as e:
            self.db.rollback()
            db_logger.error(f"查询工作空间 {workspace_id} 下的终端用户时出错: {str(e)}")
            raise

    async def get_all_by_workspace_async(self, workspace_id: uuid.UUID) -> List[EndUser]:
        """获取工作空间下的所有终端用户（异步版本）。"""
        try:
            result = await self.db.execute(
                select(EndUser).where(
                    EndUser.workspace_id == workspace_id,
                    EndUser.is_active == True,
                )
            )
            end_users = result.scalars().all()
            db_logger.debug(
                f"成功查询工作空间 {workspace_id} 下的 {len(end_users)} 个终端用户(异步)"
            )
            return list(end_users)
        except Exception as e:
            await self.db.rollback()
            db_logger.error(f"查询工作空间 {workspace_id} 下终端用户时出错(异步): {str(e)}")
            raise

    def get_cache_refresh_fields_by_workspace(
            self, workspace_id: uuid.UUID
    ) -> List[tuple]:
        """获取工作空间下所有活跃终端用户的「缓存刷新判定」所需字段（列裁剪）。

        仅取 id / write_time / memory_insight_updated_at / user_summary_updated_at 四列，
        返回普通元组而非 ORM 对象：
          - 不受 session 关闭后 detach/expire 影响（调用方可在会话外安全遍历）
          - 不加载整行，万级用户时显著降低内存

        Returns:
            List[tuple]: 每个元素为
                (id, write_time, memory_insight_updated_at, user_summary_updated_at)
        """
        try:
            rows = (
                self.db.query(
                    EndUser.id,
                    EndUser.write_time,
                    EndUser.memory_insight_updated_at,
                    EndUser.user_summary_updated_at,
                )
                .filter(EndUser.workspace_id == workspace_id, EndUser.is_active == True)
                .all()
            )
            db_logger.debug(
                f"成功查询工作空间 {workspace_id} 下 {len(rows)} 个终端用户的缓存刷新字段"
            )
            return rows
        except Exception as e:
            self.db.rollback()
            db_logger.error(f"查询工作空间 {workspace_id} 缓存刷新字段时出错: {str(e)}")
            raise

    def get_filtered_by_workspace(
            self,
            workspace_id: uuid.UUID,
            end_user_id: Optional[uuid.UUID] = None,
            other_id: Optional[str] = None,
            other_name: Optional[str] = None,
            limit: Optional[int] = None,
            offset: int = 0,
    ) -> tuple[List[EndUser], int]:
        """获取工作空间下按条件过滤的终端用户（分页）

        所有过滤条件均为可选，多个条件之间为 AND 关系。

        Args:
            workspace_id: 工作空间ID（必填）
            end_user_id: 终端用户ID（可选）
            other_id: 第三方ID（可选）
            other_name: 用户名称（可选，模糊匹配）
            limit: 每页条数（None 表示不分页）
            offset: 偏移量

        Returns:
            tuple[List[EndUser], int]: (匹配的终端用户列表, 总数量)
        """
        try:
            base_query = self.db.query(EndUser).filter(
                EndUser.workspace_id == workspace_id,
                EndUser.is_active == True,
            )

            if end_user_id is not None:
                base_query = base_query.filter(EndUser.id == end_user_id)
            if other_id is not None:
                base_query = base_query.filter(EndUser.other_id == other_id)
            if other_name is not None:
                base_query = base_query.filter(EndUser.other_name.ilike(f"%{other_name}%"))

            total = base_query.count()

            if limit is not None:
                end_users = base_query.order_by(EndUser.created_at.desc()).offset(offset).limit(limit).all()
            else:
                end_users = base_query.all()

            db_logger.info(
                f"成功按条件查询工作空间 {workspace_id} 下的 {len(end_users)} 个终端用户（共 {total} 个）"
            )
            return end_users, total
        except Exception as e:
            self.db.rollback()
            db_logger.error(f"按条件查询工作空间 {workspace_id} 下的终端用户时出错: {str(e)}")
            raise

    def get_all_active_workspaces(self) -> List[uuid.UUID]:
        """获取所有活动工作空间的ID
        
        Returns:
            List[uuid.UUID]: 活动工作空间ID列表
        """
        try:
            workspace_ids = (
                self.db.query(Workspace.id)
                .filter(Workspace.is_active)
                .all()
            )
            # 提取ID（查询返回的是元组列表）
            workspace_id_list = [workspace_id[0] for workspace_id in workspace_ids]
            db_logger.info(f"成功查询到 {len(workspace_id_list)} 个活动工作空间")
            return workspace_id_list
        except Exception as e:
            self.db.rollback()
            db_logger.error(f"查询活动工作空间时出错: {str(e)}")
            raise

    def update_memory_config_id(self, end_user_id: uuid.UUID, memory_config_id: uuid.UUID) -> bool:
        """更新终端用户的 memory_config_id（懒更新）。
        
        Args:
            end_user_id: 终端用户ID
            memory_config_id: 记忆配置ID
            
        Returns:
            bool: 更新成功返回True，否则返回False
        """
        try:
            updated_count = (
                self.db.query(EndUser)
                .filter(EndUser.id == end_user_id, EndUser.is_active == True)
                .update(
                    {EndUser.memory_config_id: memory_config_id},
                    synchronize_session=False
                )
            )
            self.db.commit()

            if updated_count > 0:
                db_logger.debug(f"成功更新终端用户 {end_user_id} 的 memory_config_id: {memory_config_id}")
                return True
            else:
                db_logger.warning(f"未找到终端用户 {end_user_id}，无法更新 memory_config_id")
                return False
        except Exception as e:
            self.db.rollback()
            db_logger.error(f"更新终端用户 {end_user_id} 的 memory_config_id 时出错: {str(e)}")
            raise

    def get_memory_config_id(self, end_user_id: uuid.UUID) -> Optional[uuid.UUID]:
        """获取终端用户的 memory_config_id。
        
        Args:
            end_user_id: 终端用户ID
            
        Returns:
            Optional[uuid.UUID]: memory_config_id 或 None
        """
        try:
            end_user = (
                self.db.query(EndUser)
                .filter(EndUser.id == end_user_id, EndUser.is_active == True)
                .first()
            )
            if end_user and end_user.memory_config_id:
                db_logger.debug(f"获取终端用户 {end_user_id} 的 memory_config_id: {end_user.memory_config_id}")
                return end_user.memory_config_id
            return None
        except Exception as e:
            self.db.rollback()
            db_logger.error(f"获取终端用户 {end_user_id} 的 memory_config_id 时出错: {str(e)}")
            raise

    # def batch_update_memory_config_id(
    #     self,
    #     app_id: uuid.UUID,
    #     memory_config_id: uuid.UUID
    # ) -> int:
    #     """批量更新应用下所有终端用户的 memory_config_id
    #
    #     Args:
    #         app_id: 应用ID
    #         memory_config_id: 新的记忆配置ID
    #
    #     Returns:
    #         int: 更新的行数
    #     """
    #     try:
    #         from sqlalchemy import update
    #
    #         stmt = (
    #             update(EndUser)
    #             .where(EndUser.app_id == app_id)
    #             .values(memory_config_id=memory_config_id)
    #         )
    #
    #         result = self.db.execute(stmt)
    #         self.db.commit()
    #
    #         updated_count = result.rowcount
    #
    #         db_logger.info(
    #             f"批量更新终端用户记忆配置: app_id={app_id}, "
    #             f"memory_config_id={memory_config_id}, updated_count={updated_count}"
    #         )
    #
    #         return updated_count
    #
    #     except Exception as e:
    #         self.db.rollback()
    #         db_logger.error(
    #             f"批量更新终端用户记忆配置时出错: app_id={app_id}, "
    #             f"memory_config_id={memory_config_id}, error={str(e)}"
    #         )
    #         raise

    def batch_update_memory_config_id_by_workspace(
            self,
            workspace_id: uuid.UUID,
            memory_config_id: uuid.UUID
    ) -> int:
        """批量更新工作空间下所有终端用户的 memory_config_id"""
        try:
            stmt = (
                update(EndUser)
                .where(EndUser.workspace_id == workspace_id, EndUser.is_active == True)
                .values(memory_config_id=memory_config_id)
            )

            result = self.db.execute(stmt)
            self.db.commit()

            updated_count = result.rowcount

            db_logger.info(
                f"批量更新终端用户记忆配置: workspace_id={workspace_id}, "
                f"memory_config_id={memory_config_id}, updated_count={updated_count}"
            )

            return updated_count
        except Exception as e:
            self.db.rollback()
            db_logger.error(
                f"批量更新终端用户记忆配置时出错: workspace_id={workspace_id}, "
                f"memory_config_id={memory_config_id}, error={str(e)}"
            )
            raise

    def batch_update_memory_config_id_by_app(
            self,
            app_id: uuid.UUID,
            memory_config_id: uuid.UUID
    ) -> int:
        """批量更新应用下所有终端用户的 memory_config_id
        
        Args:
            app_id: 应用ID
            memory_config_id: 新的记忆配置ID
            
        Returns:
            int: 更新的终端用户数量
            
        Raises:
            Exception: 数据库操作失败时抛出
        """
        try:
            stmt = (
                update(EndUser)
                .where(EndUser.app_id == app_id, EndUser.is_active == True)
                .values(memory_config_id=memory_config_id)
            )

            result = self.db.execute(stmt)
            self.db.commit()

            updated_count = result.rowcount

            db_logger.info(
                f"批量更新终端用户记忆配置: app_id={app_id}, "
                f"memory_config_id={memory_config_id}, updated_count={updated_count}"
            )

            return updated_count
        except Exception as e:
            self.db.rollback()
            db_logger.error(
                f"批量更新终端用户记忆配置时出错: app_id={app_id}, "
                f"memory_config_id={memory_config_id}, error={str(e)}"
            )
            raise

    def count_by_memory_config_id(
            self,
            memory_config_id: uuid.UUID
    ) -> int:
        """统计使用指定记忆配置的终端用户数量
        
        Args:
            memory_config_id: 记忆配置ID
            
        Returns:
            int: 使用该配置的终端用户数量
        """
        try:
            from sqlalchemy import func, select

            stmt = (
                select(func.count(EndUser.id))
                .where(EndUser.memory_config_id == memory_config_id, EndUser.is_active == True)
            )

            count = self.db.execute(stmt).scalar() or 0

            db_logger.debug(f"统计记忆配置使用数: memory_config_id={memory_config_id}, count={count}")

            return count

        except Exception as e:
            self.db.rollback()
            db_logger.error(f"统计记忆配置使用数时出错: memory_config_id={memory_config_id}, error={str(e)}")
            raise

    def clear_memory_config_id(
            self,
            memory_config_id: uuid.UUID
    ) -> int:
        """清除所有使用指定记忆配置的终端用户的 memory_config_id
        
        将 memory_config_id 设置为 NULL
        
        Args:
            memory_config_id: 要清除的记忆配置ID
            
        Returns:
            int: 清除的行数
        """
        try:
            stmt = (
                update(EndUser)
                .where(EndUser.memory_config_id == memory_config_id, EndUser.is_active == True)
                .values(memory_config_id=None)
            )

            result = self.db.execute(stmt)
            self.db.commit()

            cleared_count = result.rowcount

            db_logger.warning(
                f"清除终端用户记忆配置引用: memory_config_id={memory_config_id}, "
                f"cleared_count={cleared_count}"
            )

            return cleared_count

        except Exception as e:
            self.db.rollback()
            db_logger.error(
                f"清除终端用户记忆配置引用时出错: memory_config_id={memory_config_id}, "
                f"error={str(e)}"
            )
            raise

    def soft_delete_by_end_user_id(self, end_user_id: uuid.UUID) -> bool:
        """软删除指定 EndUser（按 end_user_id）。

        设置 is_active=False，数据保留，查询时通过 is_active=True 过滤。
        同时操作 EndUserInfo（通过 ORM cascade 或显式标记）。

        Args:
            end_user_id: 终端用户 ID

        Returns:
            bool: 是否成功删除（更新了至少一行）
        """
        try:
            updated = (
                self.db.query(EndUser)
                .filter(
                    EndUser.id == end_user_id,
                    EndUser.is_active == True,
                )
                .update(
                    {"is_active": False},
                    synchronize_session=False,
                )
            )
            self.db.commit()
            if updated:
                db_logger.info(f"软删除终端用户: end_user_id={end_user_id}")
            else:
                db_logger.warning(f"未找到活跃终端用户，无法软删除: end_user_id={end_user_id}")
            return updated > 0
        except Exception as e:
            self.db.rollback()
            db_logger.error(f"软删除终端用户失败: end_user_id={end_user_id}, error={str(e)}")
            raise

    async def soft_delete_by_end_user_id_async(self, end_user_id: uuid.UUID) -> bool:
        """软删除指定 EndUser（异步版本）"""
        try:
            result = await self.db.execute(
                update(EndUser)
                .where(EndUser.id == end_user_id, EndUser.is_active == True)
                .values(is_active=False)
            )
            await self.db.commit()
            if result.rowcount:
                db_logger.info(f"软删除终端用户(异步): end_user_id={end_user_id}")
            else:
                db_logger.warning(f"未找到活跃终端用户(异步)，无法软删除: end_user_id={end_user_id}")
            return result.rowcount > 0
        except Exception as e:
            await self.db.rollback()
            db_logger.error(f"软删除终端用户失败(异步): end_user_id={end_user_id}, error={str(e)}")
            raise

    def soft_delete_by_user_id(self, user_id: uuid.UUID) -> int:
        """软删除指定 User（通过 other_id 关联）的所有 EndUser。

        设置 is_active=False，数据保留，查询时通过 is_active=True 过滤。

        Args:
            user_id: users 表中的用户 ID

        Returns:
            int: 软删除的记录数
        """
        try:
            user_id_str = str(user_id)
            updated = (
                self.db.query(EndUser)
                .filter(
                    EndUser.other_id == user_id_str,
                    EndUser.is_active == True,
                )
                .update(
                    {"is_active": False},
                    synchronize_session=False,
                )
            )
            self.db.commit()
            db_logger.info(f"软删除终端用户: user_id={user_id_str}, count={updated}")
            return updated
        except Exception as e:
            self.db.rollback()
            db_logger.error(f"软删除终端用户失败: user_id={user_id}, error={str(e)}")
            raise

    def get_all_active(self) -> List[EndUser]:
        """获取所有活跃的 EndUser 记录"""
        try:
            end_users = (
                self.db.query(EndUser)
                .filter(EndUser.is_active == True)
                .all()
            )
            db_logger.info(f"查询所有活跃终端用户: {len(end_users)} 个")
            return end_users
        except Exception as e:
            self.db.rollback()
            db_logger.error(f"查询所有活跃终端用户时出错: {str(e)}")
            raise

    async def get_all_active_async(self) -> List[EndUser]:
        """获取所有活跃的 EndUser 记录（异步版本）。"""
        try:
            result = await self.db.execute(
                select(EndUser).where(EndUser.is_active == True)
            )
            end_users = list(result.scalars().all())
            db_logger.info(f"查询所有活跃终端用户(异步): {len(end_users)} 个")
            return end_users
        except Exception as e:
            await self.db.rollback()
            db_logger.error(f"查询所有活跃终端用户时出错(异步): {str(e)}")
            raise

    def get_ids_by_app_workspace(self, workspace_id: uuid.UUID) -> List[str]:
        """通过 App 关联查询指定 workspace 下的所有活跃 end_user ID"""
        from app.models.app_model import App
        try:
            rows = (
                self.db.query(EndUser.id)
                .join(App, EndUser.app_id == App.id)
                .filter(
                    App.workspace_id == workspace_id,
                    EndUser.is_active == True,
                )
                .all()
            )
            return [str(eid) for (eid,) in rows]
        except Exception as e:
            self.db.rollback()
            db_logger.error(f"查询 workspace {workspace_id} 下的终端用户ID时出错: {str(e)}")
            raise

    def get_config_batch_by_ids(self, end_user_ids: List[uuid.UUID]):
        """批量查询 end_user 配置信息（连 App 表获取 workspace_id），返回原始 SQLAlchemy 行"""
        from app.models.app_model import App
        try:
            return (
                self.db.query(
                    EndUser.id.label("end_user_id"),
                    EndUser.memory_config_id.label("memory_config_id"),
                    EndUser.workspace_id.label("end_user_workspace_id"),
                    App.workspace_id.label("app_workspace_id"),
                )
                .outerjoin(App, App.id == EndUser.app_id)
                .filter(EndUser.id.in_(end_user_ids), EndUser.is_active == True)
                .all()
            )
        except Exception as e:
            self.db.rollback()
            db_logger.error(f"批量查询配置信息时出错: {str(e)}")
            raise

    def get_paginated_with_memory(
            self,
            workspace_id: uuid.UUID,
            page: int,
            pagesize: int,
            keyword: Optional[str] = None,
            label: Optional[str] = None,
    ) -> tuple[List[EndUser], int]:
        """Dashboard 专用：分页查询有记忆的宿主（memory_count > 0）

        返回结果按 created_at 从新到旧排序（NULL 值排在最后），
        只加载接口所需列以避免加载大 Text 字段。

        Args:
            workspace_id: 工作空间ID
            page: 页码（从1开始）
            pagesize: 每页数量
            keyword: 搜索关键词（可选，同时模糊匹配 other_name 和 id）
            label: 标签过滤（可选，"long" 表示有 other_name，"short" 表示无 other_name）

        Returns:
            tuple[List[EndUser], int]: (当前页宿主列表, 符合条件的总数)
        """
        from sqlalchemy import desc, String, cast
        from sqlalchemy.orm import load_only
        from sqlalchemy.sql.expression import nullslast

        columns = load_only(
            EndUser.id,
            EndUser.other_name,
            EndUser.memory_tags,
            EndUser.memory_count,
            EndUser.app_id,
            EndUser.memory_config_id,
            EndUser.created_at,
            EndUser.workspace_id,
        )
        query = self.db.query(EndUser).options(columns).filter(
            EndUser.workspace_id == workspace_id,
            EndUser.memory_count > 0,
            EndUser.is_active == True,
        )

        if label == "long":
            query = query.filter(
                EndUser.other_name.isnot(None),
                EndUser.other_name != "",
            )
        elif label == "short":
            query = query.filter(
                or_(
                    EndUser.other_name.is_(None),
                    EndUser.other_name == "",
                )
            )

        if keyword:
            keyword = keyword.strip()
        if keyword:
            pattern = f"%{keyword}%"
            query = query.filter(
                or_(
                    EndUser.other_name.ilike(pattern),
                    cast(EndUser.id, String).ilike(pattern),
                )
            )

        total = query.count()
        if total == 0:
            return [], 0

        items = (
            query.order_by(nullslast(desc(EndUser.created_at)), desc(EndUser.id))
            .offset((page - 1) * pagesize)
            .limit(pagesize)
            .all()
        )
        return items, total

    def get_paginated_with_memory_rag(
            self,
            workspace_id: uuid.UUID,
            page: int,
            pagesize: int,
            keyword: Optional[str] = None,
            label: Optional[str] = None,
    ) -> tuple[list, int]:
        """Dashboard RAG 模式：分页查询有记忆的宿主

        RAG 记忆数量以 documents.chunk_num 为准：
        - file_name = end_user_id + ".txt"
        - 只统计当前 workspace 下 permission_id="Memory" 的用户记忆知识库
        - 在 SQL 层过滤 chunk 总数为 0 的宿主

        Args:
            workspace_id: 工作空间ID
            page: 页码（从1开始）
            pagesize: 每页数量
            keyword: 搜索关键词（可选，同时模糊匹配 other_name 和 id）
            label: 标签过滤（可选，"long" 表示有 other_name，"short" 表示无 other_name）

        Returns:
            tuple[list, int]: (items列表[{"end_user": ORM, "memory_count": int}], 总数)
        """
        from sqlalchemy import desc, String, cast, func
        from sqlalchemy.orm import load_only
        from sqlalchemy.sql.expression import nullslast
        from app.models.document_model import Document
        from app.models.knowledge_model import Knowledge

        chunk_subquery = (
            self.db.query(
                Document.file_name.label("file_name"),
                func.coalesce(func.sum(Document.chunk_num), 0).label("memory_count"),
            )
            .join(Knowledge, Document.kb_id == Knowledge.id)
            .filter(
                Knowledge.workspace_id == workspace_id,
                Knowledge.status == 1,
                Knowledge.permission_id == "Memory",
                Document.status == 1,
            )
            .group_by(Document.file_name)
            .subquery()
        )

        columns = load_only(
            EndUser.id,
            EndUser.other_name,
            EndUser.memory_tags,
            EndUser.memory_count,
            EndUser.app_id,
            EndUser.memory_config_id,
            EndUser.created_at,
            EndUser.workspace_id,
        )

        base_query = (
            self.db.query(
                EndUser,
                chunk_subquery.c.memory_count.label("memory_count"),
            )
            .options(columns)
            .join(
                chunk_subquery,
                chunk_subquery.c.file_name == func.concat(cast(EndUser.id, String), ".txt"),
            )
            .filter(
                EndUser.workspace_id == workspace_id,
                chunk_subquery.c.memory_count > 0,
                EndUser.is_active == True,
            )
        )

        if label == "long":
            base_query = base_query.filter(
                EndUser.other_name.isnot(None),
                EndUser.other_name != "",
            )
        elif label == "short":
            base_query = base_query.filter(
                or_(
                    EndUser.other_name.is_(None),
                    EndUser.other_name == "",
                )
            )

        if keyword:
            keyword = keyword.strip()
        if keyword:
            pattern = f"%{keyword}%"
            base_query = base_query.filter(
                or_(
                    EndUser.other_name.ilike(pattern),
                    cast(EndUser.id, String).ilike(pattern),
                )
            )

        total = base_query.count()
        if total == 0:
            return [], 0

        rows = (
            base_query.order_by(nullslast(desc(EndUser.created_at)), desc(EndUser.id))
            .offset((page - 1) * pagesize)
            .limit(pagesize)
            .all()
        )

        items = []
        for end_user_orm, memory_count in rows:
            items.append({
                "end_user": end_user_orm,
                "memory_count": int(memory_count or 0),
            })
        return items, total

    def update_memory_count(self, end_user_id: uuid.UUID, node_count: int) -> bool:
        """更新终端用户的记忆节点计数（仅活跃用户）"""
        try:
            updated = (
                self.db.query(EndUser)
                .filter(EndUser.id == end_user_id, EndUser.is_active == True)
                .update({"memory_count": node_count}, synchronize_session=False)
            )
            self.db.commit()
            return updated > 0
        except Exception as e:
            self.db.rollback()
            db_logger.error(f"更新记忆计数失败: end_user_id={end_user_id}, error={str(e)}")
            raise

    async def update_memory_count_async(self, end_user_id: uuid.UUID, node_count: int) -> bool:
        """更新终端用户的记忆节点计数（异步版本）"""
        try:
            result = await self.db.execute(
                update(EndUser)
                .where(EndUser.id == end_user_id, EndUser.is_active == True)
                .values(memory_count=node_count)
            )
            await self.db.commit()
            return result.rowcount > 0
        except Exception as e:
            await self.db.rollback()
            db_logger.error(f"更新记忆计数失败(异步): end_user_id={end_user_id}, error={str(e)}")
            raise


# def get_end_users_by_app_id(db: Session, app_id: uuid.UUID) -> List[EndUser]:
#     """根据应用ID查询宿主（返回 EndUser ORM 列表）"""
#     repo = EndUserRepository(db)
#     end_users = repo.get_end_users_by_app_id(app_id)
#     return end_users

def get_end_users_by_workspace(db: Session, workspace_id: uuid.UUID) -> List[EndUser]:
    """根据工作空间ID查询终端用户（返回 EndUser ORM 列表）"""
    repo = EndUserRepository(db)
    end_users = repo.get_end_users_by_workspace(workspace_id)
    return end_users


def get_end_users_count_by_workspace(db: Session, workspace_id: uuid.UUID) -> int:
    repo = EndUserRepository(db)
    end_users_count = repo.get_end_users_count_by_workspace(workspace_id)
    return end_users_count


def get_end_user_by_id(db: Session, end_user_id: uuid.UUID) -> Optional[EndUser]:
    """根据 end_user_id 查询对应宿主"""
    repo = EndUserRepository(db)
    end_user = repo.get_end_user_by_id(end_user_id)
    return end_user


async def get_end_user_by_id_async(db: AsyncSession, end_user_id: uuid.UUID) -> Optional[EndUser]:
    repo = EndUserRepository(db)
    end_user = await repo.get_end_user_by_id_async(end_user_id)
    return end_user


# @redis_cache(ttl=600, prefix='tenant', skip_args=["db"])
def get_tenant_id_by_end_user_id(db: Session, end_user_id: uuid.UUID) -> Optional[uuid.UUID]:
    stmt = (
        select(Workspace.tenant_id)
        .join(EndUser, EndUser.workspace_id == Workspace.id)
        .filter(EndUser.id == end_user_id, EndUser.is_active == True)
    )
    result = db.execute(stmt)
    return result.scalar()


# @redis_cache(ttl=600, prefix='tenant', skip_args=["db"])
async def get_tenant_id_by_end_user_id_async(db: AsyncSession, end_user_id: uuid.UUID) -> Optional[uuid.UUID]:
    stmt = (
        select(Workspace.tenant_id)
        .join(EndUser, EndUser.workspace_id == Workspace.id)
        .filter(EndUser.id == end_user_id, EndUser.is_active == True)
    )
    result = await db.execute(stmt)
    return result.scalar()


# 新增的缓存操作函数（保持与类方法一致的接口）
def get_by_id(db: Session, end_user_id: uuid.UUID) -> Optional[EndUser]:
    """根据ID获取终端用户（用于缓存操作）"""
    repo = EndUserRepository(db)
    return repo.get_by_id(end_user_id)


def update_memory_insight(
        db: Session,
        end_user_id: uuid.UUID,
        memory_insight: str,
        behavior_pattern: str,
        key_findings: str,
        growth_trajectory: str
) -> bool:
    """更新记忆洞察缓存（四个维度）"""
    repo = EndUserRepository(db)
    return repo.update_memory_insight(end_user_id, memory_insight, behavior_pattern, key_findings, growth_trajectory)


def update_user_summary(
        db: Session,
        end_user_id: uuid.UUID,
        user_summary: str,
        personality: str,
        core_values: str,
        one_sentence: str
) -> bool:
    """更新用户摘要缓存（四个部分）"""
    repo = EndUserRepository(db)
    return repo.update_user_summary(end_user_id, user_summary, personality, core_values, one_sentence)


def get_all_by_workspace(db: Session, workspace_id: uuid.UUID) -> List[EndUser]:
    """获取工作空间的所有终端用户"""
    repo = EndUserRepository(db)
    return repo.get_all_by_workspace(workspace_id)


def get_all_active_workspaces(db: Session) -> List[uuid.UUID]:
    """获取所有活动工作空间的ID"""
    repo = EndUserRepository(db)
    return repo.get_all_active_workspaces()


def update_memory_config_id(db: Session, end_user_id: uuid.UUID, memory_config_id: uuid.UUID) -> bool:
    """更新终端用户的 memory_config_id（懒更新）。
    
    Args:
        db: 数据库会话
        end_user_id: 终端用户ID
        memory_config_id: 记忆配置ID
        
    Returns:
        bool: 更新成功返回True，否则返回False
    """
    repo = EndUserRepository(db)
    return repo.update_memory_config_id(end_user_id, memory_config_id)
