"""API Key Repository"""
import uuid
from typing import Optional, List, Tuple

from sqlalchemy import select, func, and_, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, joinedload

from app.core.utils.datetime_utils import utcnow_naive
from app.models.api_key_model import ApiKey, ApiKeyLog
from app.schemas import api_key_schema


class ApiKeyRepository:
    """API Key 数据访问层"""

    @staticmethod
    def create(db: Session, api_key_data: dict) -> ApiKey:
        """创建 API Key"""
        api_key = ApiKey(**api_key_data)
        db.add(api_key)
        db.flush()
        return api_key

    @staticmethod
    def get_by_id(db: Session, api_key_id: uuid.UUID) -> Optional[ApiKey]:
        """根据 ID 获取 API Key"""
        return db.get(ApiKey, api_key_id)

    @staticmethod
    async def get_by_id_async(db: AsyncSession, api_key_id: uuid.UUID) -> Optional[ApiKey]:
        """Async version of get_by_id with creator eager-loaded."""
        stmt = select(ApiKey).options(joinedload(ApiKey.creator)).where(ApiKey.id == api_key_id)
        result = await db.execute(stmt)
        return result.scalars().first()

    @staticmethod
    def get_by_api_key(db: Session, api_key: str) -> Optional[ApiKey]:
        """根据 API Key 获取 API Key"""
        stmt = select(ApiKey).where(ApiKey.api_key == api_key)
        return db.scalars(stmt).first()

    @staticmethod
    async def get_by_api_key_async(db: AsyncSession, api_key: str) -> Optional[ApiKey]:
        """Async version of get_by_api_key."""
        stmt = select(ApiKey).where(ApiKey.api_key == api_key)
        result = await db.execute(stmt)
        return result.scalars().first()

    @staticmethod
    def list_by_workspace(
            db: Session,
            workspace_id: uuid.UUID,
            query: api_key_schema.ApiKeyQuery
    ) -> Tuple[List[ApiKey], int]:
        """列出工作空间的 API Keys"""
        stmt = select(ApiKey).where(ApiKey.workspace_id == workspace_id)

        # 过滤条件
        if query.type:
            stmt = stmt.where(ApiKey.type == query.type)
        if query.is_active is not None:
            stmt = stmt.where(ApiKey.is_active == query.is_active)
        if query.resource_id:
            stmt = stmt.where(ApiKey.resource_id == query.resource_id)

        # 总数
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = db.execute(count_stmt).scalar()

        # 分页
        stmt = stmt.order_by(ApiKey.created_at.desc())
        stmt = stmt.offset((query.page - 1) * query.pagesize).limit(query.pagesize)

        items = db.scalars(stmt).all()
        return list(items), total

    @staticmethod
    def update(db: Session, api_key_id: uuid.UUID, update_data: dict) -> ApiKey | None:
        """更新 API Key"""
        allow_none_fields = {"description", "quota_limit", "expires_at"}
        api_key = db.get(ApiKey, api_key_id)
        if api_key:
            for key, value in update_data.items():
                if key in allow_none_fields:
                    setattr(api_key, key, value)
                else:
                    if value is not None:
                        setattr(api_key, key, value)
            db.flush()
        return api_key

    @staticmethod
    def delete(db: Session, api_key_id: uuid.UUID) -> bool:
        """逻辑删除 API Key"""
        api_key = db.get(ApiKey, api_key_id)
        if api_key:
            api_key.is_active = False
            db.flush()
            return True
        return False

    @staticmethod
    async def bump_usage_async(
        db: AsyncSession,
        counts: dict[uuid.UUID, int],
        *,
        last_used_at=None,
    ) -> int:
        """按 api_key_id 批量**原子自增**用量（usage_count / quota_used）。

        为什么不用 ORM 读改写（``db.get`` + ``+= 1`` + flush）：

        1. **丢更新**：并发事务各自读到旧值再写绝对值，自增互相覆盖——配额判断
           （``quota_used >= quota_limit``）因此偏松，配额可被超用；
        2. **死锁**：一个批里有多个 Key 时事务会锁多行，两个事务按相反顺序锁同样的
           行即成环（线上已复现 ``DeadlockDetectedError``：同一个 batch 内的
           ``UPDATE api_keys`` 互相等锁）。

        改为单条 ``UPDATE ... SET usage_count = usage_count + n``（PG 在行锁下
        自增是原子的，不再需要先 SELECT），并按 api_key_id **排序**后逐条执行，
        使锁顺序单调一致 → 不成环。

        Args:
            counts: ``{api_key_id: 增量}``，调用方按批聚合（同一 Key 只进来一次）。
            last_used_at: 统一写入的最后使用时间；None 取当前 UTC naive。

        Returns:
            实际更新的行数（Key 不存在或增量为 0 的不计入）。
        """
        if not counts:
            return 0

        ts = last_used_at or utcnow_naive()
        updated = 0
        # 注意：uuid.UUID 不支持比较，必须显式给 key，否则 sorted() 抛 TypeError
        for key_id in sorted(counts, key=str):
            delta = int(counts[key_id])
            if delta <= 0:
                continue
            result = await db.execute(
                update(ApiKey)
                .where(ApiKey.id == key_id)
                .values(
                    usage_count=ApiKey.usage_count + delta,
                    quota_used=ApiKey.quota_used + delta,
                    last_used_at=ts,
                )
                .execution_options(synchronize_session=False)
            )
            updated += result.rowcount or 0
        return updated

    @staticmethod
    def update_usage(db: Session, api_key_id: uuid.UUID) -> bool:
        """更新使用统计（同步版，原子自增；与异步版同源，避免丢更新）。"""
        result = db.execute(
            update(ApiKey)
            .where(ApiKey.id == api_key_id)
            .values(
                usage_count=ApiKey.usage_count + 1,
                quota_used=ApiKey.quota_used + 1,
                last_used_at=utcnow_naive(),
            )
            .execution_options(synchronize_session=False)
        )
        return bool(result.rowcount)

    @staticmethod
    async def update_usage_async(db: AsyncSession, api_key_id: uuid.UUID) -> bool:
        """单 Key 自增一次（走批量原子自增，避免读改写）。"""
        return await ApiKeyRepository.bump_usage_async(db, {api_key_id: 1}) > 0

    @staticmethod
    def get_stats(db: Session, api_key_id: uuid.UUID) -> dict:
        """获取使用统计"""
        api_key = db.get(ApiKey, api_key_id)
        if not api_key:
            return {}

        # 今日请求数
        today_start = utcnow_naive().replace(hour=0, minute=0, second=0, microsecond=0)
        today_count_stmt = select(func.count()).select_from(ApiKeyLog).where(
            and_(
                ApiKeyLog.api_key_id == api_key_id,
                ApiKeyLog.created_at >= today_start
            )
        )
        requests_today = db.execute(today_count_stmt).scalar() or 0

        # 平均响应时间
        avg_time_stmt = select(func.avg(ApiKeyLog.response_time)).where(
            ApiKeyLog.api_key_id == api_key_id
        )
        avg_response_time = db.execute(avg_time_stmt).scalar()

        return {
            "total_requests": api_key.usage_count,
            "requests_today": requests_today,
            "quota_used": api_key.quota_used,
            "quota_limit": api_key.quota_limit,
            "last_used_at": api_key.last_used_at,
            "rate_limit": api_key.rate_limit,
            "avg_response_time": float(avg_response_time) if avg_response_time else None
        }


class ApiKeyLogRepository:
    """API Key 日志数据访问层"""

    @staticmethod
    def create(db: Session, log_data: dict) -> ApiKeyLog:
        """创建日志"""
        log = ApiKeyLog(**log_data)
        db.add(log)
        db.flush()
        return log

    @staticmethod
    async def create_async(db: AsyncSession, log_data: dict) -> ApiKeyLog:
        """Async version of create."""
        log = ApiKeyLog(**log_data)
        db.add(log)
        await db.flush()
        return log

    @staticmethod
    def list_by_api_key(
            db: Session,
            api_key_id: uuid.UUID,
            filters: dict,
            page: int,
            pagesize: int
    ) -> Tuple[List[ApiKeyLog], int]:
        """
        根据 API Key ID 查询日志列表
        
        Args:
            db: 数据库会话
            api_key_id: API Key ID
            filters: 过滤条件字典，支持：
                - start_date: 开始日期
                - end_date: 结束日期
                - status_code: HTTP 状态码
                - endpoint: 端点路径
            page: 页码
            pagesize: 每页数量
            
        Returns:
            Tuple[List[ApiKeyLog], int]: (日志列表, 总数)
        """
        stmt = select(ApiKeyLog).where(ApiKeyLog.api_key_id == api_key_id)

        # 应用过滤条件
        if filters.get('start_date'):
            stmt = stmt.where(ApiKeyLog.created_at >= filters['start_date'])

        if filters.get('end_date'):
            stmt = stmt.where(ApiKeyLog.created_at <= filters['end_date'])

        if filters.get('status_code'):
            stmt = stmt.where(ApiKeyLog.status_code == filters['status_code'])

        if filters.get('endpoint'):
            stmt = stmt.where(ApiKeyLog.endpoint.ilike(f"%{filters['endpoint']}%"))

        # 计算总数
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = db.execute(count_stmt).scalar()

        # 分页和排序
        stmt = stmt.order_by(ApiKeyLog.created_at.desc())
        stmt = stmt.offset((page - 1) * pagesize).limit(pagesize)

        items = db.scalars(stmt).all()
        return list(items), total
