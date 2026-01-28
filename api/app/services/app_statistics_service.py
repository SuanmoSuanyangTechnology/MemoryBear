"""应用统计服务"""
from datetime import datetime, timedelta
from typing import Dict, Any, List
import uuid
from sqlalchemy import func, and_, cast, Date
from sqlalchemy.orm import Session

from app.models.conversation_model import Conversation, Message
from app.models.end_user_model import EndUser
from app.models.api_key_model import ApiKey, ApiKeyLog
from app.core.exceptions import BusinessException
from app.core.error_codes import BizCode


class AppStatisticsService:
    """应用统计服务"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def get_app_statistics(
        self,
        app_id: uuid.UUID,
        workspace_id: uuid.UUID,
        start_date: int,
        end_date: int
    ) -> Dict[str, Any]:
        """获取应用统计数据
        
        Args:
            app_id: 应用ID
            workspace_id: 工作空间ID
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
        token_stats = self._get_token_statistics(app_id, start_dt, end_dt)
        
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
                EndUser.created_at < end_dt
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
        app_id: uuid.UUID,
        start_dt: datetime,
        end_dt: datetime
    ) -> Dict[str, Any]:
        """获取Token消耗统计（从Message的meta_data中提取）"""
        from sqlalchemy import text
        
        # 查询所有相关消息的token使用情况
        # meta_data中可能包含: {"usage": {"total_tokens": 100}} 或 {"tokens": 100}
        daily_query = self.db.query(
            cast(Message.created_at, Date).label('date'),
            Message.meta_data
        ).join(
            Conversation, Message.conversation_id == Conversation.id
        ).filter(
            and_(
                Conversation.app_id == app_id,
                Message.created_at >= start_dt,
                Message.created_at < end_dt,
                Message.meta_data.isnot(None)
            )
        ).all()
        
        # 按日期聚合token
        daily_tokens = {}
        for row in daily_query:
            date_str = str(row.date)
            meta = row.meta_data or {}
            
            # 提取token数量（支持多种格式）
            tokens = 0
            if isinstance(meta, dict):
                # 格式1: {"usage": {"total_tokens": 100}}
                if "usage" in meta and isinstance(meta["usage"], dict):
                    tokens = meta["usage"].get("total_tokens", 0)
                # 格式2: {"tokens": 100}
                elif "tokens" in meta:
                    tokens = meta.get("tokens", 0)
                # 格式3: {"total_tokens": 100}
                elif "total_tokens" in meta:
                    tokens = meta.get("total_tokens", 0)
            
            if date_str not in daily_tokens:
                daily_tokens[date_str] = 0
            daily_tokens[date_str] += int(tokens)
        
        daily_data = [{"date": date, "tokens": tokens} for date, tokens in sorted(daily_tokens.items()) if tokens != 0]
        total = sum(row["tokens"] for row in daily_data)
        
        return {"daily": daily_data, "total": total}
