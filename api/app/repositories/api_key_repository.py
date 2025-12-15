"""API Key Repository"""
import uuid
import datetime
from typing import Optional, List, Tuple

from sqlalchemy.orm import Session
from sqlalchemy import select, func, and_

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
    def get_by_hash(db: Session, key_hash: str) -> Optional[ApiKey]:
        """根据哈希值获取 API Key"""
        stmt = select(ApiKey).where(ApiKey.key_hash == key_hash)
        return db.scalars(stmt).first()

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
        api_key = db.get(ApiKey, api_key_id)
        if api_key:
            for key, value in update_data.items():
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
    def update_usage(db: Session, api_key_id: uuid.UUID) -> bool:
        """更新使用统计"""
        api_key = db.get(ApiKey, api_key_id)
        if api_key:
            api_key.usage_count += 1
            api_key.quota_used += 1
            api_key.last_used_at = datetime.datetime.now()
            db.flush()
            return True
        return False

    @staticmethod
    def get_stats(db: Session, api_key_id: uuid.UUID) -> dict:
        """获取使用统计"""
        api_key = db.get(ApiKey, api_key_id)
        if not api_key:
            return {}

        # 今日请求数
        today_start = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
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
