"""应用统计服务"""
from datetime import datetime, timedelta
from typing import Dict, Any
import uuid
from sqlalchemy import func, and_, cast, Date
from sqlalchemy.orm import Session

from app.models.conversation_model import Conversation
from app.models.end_user_model import EndUser
from app.models.api_key_model import ApiKey, ApiKeyLog, ApiKeyType
from app.models.model_usage_record import ModelUsageRecord


class AppStatisticsService:
    """应用统计服务"""

    def __init__(self, db: Session):
        self.db = db

    def get_app_statistics(
        self,
        app_id: uuid.UUID,
        workspace_id: uuid.UUID,
        tenant_id: uuid.UUID,
        start_date: int,
        end_date: int
    ) -> Dict[str, Any]:
        """获取应用统计数据

        Args:
            app_id: 应用ID
            workspace_id: 工作空间ID
            tenant_id: 租户ID（token 段按租户隔离）
            start_date: 开始时间戳（毫秒）
            end_date: 结束时间戳（毫秒）

        Returns:
            统计数据字典
        """
        # 将毫秒时间戳转换为 datetime
        start_dt = datetime.fromtimestamp(start_date / 1000)
        end_dt = datetime.fromtimestamp(end_date / 1000) + timedelta(days=1)

        # 1. 会话统计
        conversations_stats = self._get_conversations_statistics(app_id, workspace_id, start_dt, end_dt)

        # 2. 新增用户统计
        users_stats = self._get_new_users_statistics(app_id, start_dt, end_dt)

        # 3. API调用统计
        api_stats = self._get_api_calls_statistics(app_id, start_dt, end_dt)

        # 4. Token消耗统计
        token_stats = self._get_token_statistics(tenant_id, app_id, start_dt, end_dt)
        
        return {
            "daily_conversations": conversations_stats["daily"],
            "total_conversations": conversations_stats["total"],
            "daily_new_users": users_stats["daily"],
            "total_new_users": users_stats["total"],
            "daily_api_calls": api_stats["daily"],
            "total_api_calls": api_stats["total"],
            "daily_tokens": token_stats["daily"],
            "total_tokens": token_stats["total"]
        }
    
    def _get_conversations_statistics(
        self,
        app_id: uuid.UUID,
        workspace_id: uuid.UUID,
        start_dt: datetime,
        end_dt: datetime
    ) -> Dict[str, Any]:
        """获取会话统计"""
        # 每日会话数
        daily_query = self.db.query(
            cast(Conversation.created_at, Date).label('date'),
            func.count(Conversation.id).label('count')
        ).filter(
            and_(
                Conversation.app_id == app_id,
                Conversation.workspace_id == workspace_id,
                Conversation.created_at >= start_dt,
                Conversation.created_at < end_dt
            )
        ).group_by(cast(Conversation.created_at, Date)).all()
        
        daily_data = [{"date": str(row.date), "count": row.count} for row in daily_query]
        total = sum(row["count"] for row in daily_data)
        
        return {"daily": daily_data, "total": total}
    
    def _get_new_users_statistics(
        self,
        app_id: uuid.UUID,
        start_dt: datetime,
        end_dt: datetime
    ) -> Dict[str, Any]:
        """获取新增用户统计"""
        # 每日新增用户数
        daily_query = self.db.query(
            cast(EndUser.created_at, Date).label('date'),
            func.count(EndUser.id).label('count')
        ).filter(
            and_(
                EndUser.app_id == app_id,
                EndUser.created_at >= start_dt,
                EndUser.created_at < end_dt,
                EndUser.is_active == True,
            )
        ).group_by(cast(EndUser.created_at, Date)).all()
        
        daily_data = [{"date": str(row.date), "count": row.count} for row in daily_query]
        total = sum(row["count"] for row in daily_data)
        
        return {"daily": daily_data, "total": total}
    
    def _get_api_calls_statistics(
        self,
        app_id: uuid.UUID,
        start_dt: datetime,
        end_dt: datetime
    ) -> Dict[str, Any]:
        """获取API调用统计"""
        # 每日API调用次数
        daily_query = self.db.query(
            cast(ApiKeyLog.created_at, Date).label('date'),
            func.count(ApiKeyLog.id).label('count')
        ).join(
            ApiKey, ApiKeyLog.api_key_id == ApiKey.id
        ).filter(
            and_(
                ApiKey.resource_id == app_id,
                ApiKeyLog.created_at >= start_dt,
                ApiKeyLog.created_at < end_dt
            )
        ).group_by(cast(ApiKeyLog.created_at, Date)).all()
        
        daily_data = [{"date": str(row.date), "count": row.count} for row in daily_query]
        total = sum(row["count"] for row in daily_data)
        
        return {"daily": daily_data, "total": total}
    
    def _get_token_statistics(
        self,
        tenant_id: uuid.UUID,
        app_id: uuid.UUID,
        start_dt: datetime,
        end_dt: datetime
    ) -> Dict[str, Any]:
        """获取Token消耗统计（真源：model_usage_records 计量落表；口径=该 app 的 llm 调用）。

        替换旧口径（读 messages.meta_data）后覆盖试运行/工作流等全部 app 归因调用，
        与成本/配额口径对齐（后者在外部网关，不在本服务）。
        """
        token_sum = func.sum(
            ModelUsageRecord.input_tokens + ModelUsageRecord.output_tokens
        )
        daily_query = self.db.query(
            cast(ModelUsageRecord.created_at, Date).label('date'),
            token_sum.label('tokens')
        ).filter(
            and_(
                ModelUsageRecord.tenant_id == tenant_id,
                ModelUsageRecord.resource_type == "app",
                ModelUsageRecord.resource_id == app_id,
                ModelUsageRecord.capability == "llm",
                ModelUsageRecord.created_at >= start_dt,
                ModelUsageRecord.created_at < end_dt
            )
        ).group_by(
            cast(ModelUsageRecord.created_at, Date)
        ).having(token_sum > 0).order_by(cast(ModelUsageRecord.created_at, Date)).all()

        daily_data = [{"date": str(row.date), "count": int(row.tokens)} for row in daily_query]
        total = sum(row["count"] for row in daily_data)

        return {"daily": daily_data, "total": total}
    
    def get_workspace_api_statistics(
        self,
        workspace_id: uuid.UUID,
        start_date: int,
        end_date: int
    ) -> list[Any]:
        """获取工作空间API调用统计
        
        Args:
            workspace_id: 工作空间ID
            start_date: 开始时间戳（毫秒）
            end_date: 结束时间戳（毫秒）
        
        Returns:
            每日统计数据列表
        """
        # 将毫秒时间戳转换为 datetime
        start_time = datetime.fromtimestamp(start_date / 1000)
        end_time = datetime.fromtimestamp(end_date / 1000)
        
        # 应用类型（agent, multi_agent, workflow）
        app_types = [ApiKeyType.AGENT, ApiKeyType.CLUSTER, ApiKeyType.WORKFLOW]
        
        # 每日应用类型调用次数
        daily_app_calls = self.db.query(
            cast(ApiKeyLog.created_at, Date).label('date'),
            func.count(ApiKeyLog.id).label('count')
        ).join(
            ApiKey, ApiKeyLog.api_key_id == ApiKey.id
        ).filter(
            and_(
                ApiKey.workspace_id == workspace_id,
                ApiKey.type.in_(app_types),
                ApiKeyLog.created_at >= start_time,
                ApiKeyLog.created_at <= end_time
            )
        ).group_by(cast(ApiKeyLog.created_at, Date)).all()
        
        # 每日服务类型调用次数
        daily_service_calls = self.db.query(
            cast(ApiKeyLog.created_at, Date).label('date'),
            func.count(ApiKeyLog.id).label('count')
        ).join(
            ApiKey, ApiKeyLog.api_key_id == ApiKey.id
        ).filter(
            and_(
                ApiKey.workspace_id == workspace_id,
                ApiKey.type == ApiKeyType.SERVICE,
                ApiKeyLog.created_at >= start_time,
                ApiKeyLog.created_at <= end_time
            )
        ).group_by(cast(ApiKeyLog.created_at, Date)).all()
        
        # 构建每日数据
        app_calls_dict = {str(row.date): row.count for row in daily_app_calls}
        service_calls_dict = {str(row.date): row.count for row in daily_service_calls}
        
        # 合并所有日期
        all_dates = sorted(set(app_calls_dict.keys()) | set(service_calls_dict.keys()))
        
        daily_data = []
        for date in all_dates:
            app_count = app_calls_dict.get(date, 0)
            service_count = service_calls_dict.get(date, 0)
            daily_data.append({
                "date": date,
                "total_calls": app_count + service_count,
                "app_calls": app_count,
                "service_calls": service_count
            })
        
        return daily_data
