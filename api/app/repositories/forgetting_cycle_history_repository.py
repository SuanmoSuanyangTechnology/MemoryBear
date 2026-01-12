"""
遗忘周期历史记录仓储

提供遗忘周期历史记录的数据访问操作。
"""

from typing import List, Optional
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import desc, and_

from app.models.forgetting_cycle_history_model import ForgettingCycleHistory


class ForgettingCycleHistoryRepository:
    """遗忘周期历史记录仓储类"""
    
    def create(
        self,
        db: Session,
        end_user_id: str,
        execution_time: datetime,
        merged_count: int,
        failed_count: int,
        average_activation_value: Optional[float],
        total_nodes: int,
        low_activation_nodes: int,
        duration_seconds: float,
        trigger_type: str = "manual"
    ) -> ForgettingCycleHistory:
        """
        创建历史记录
        
        Args:
            db: 数据库会话
            end_user_id: 终端用户ID
            execution_time: 执行时间
            merged_count: 融合节点数
            failed_count: 失败节点数
            average_activation_value: 平均激活值
            total_nodes: 总节点数
            low_activation_nodes: 低激活值节点数
            duration_seconds: 执行耗时
            trigger_type: 触发类型
        
        Returns:
            ForgettingCycleHistory: 创建的历史记录
        """
        history = ForgettingCycleHistory(
            end_user_id=end_user_id,
            execution_time=execution_time,
            merged_count=merged_count,
            failed_count=failed_count,
            average_activation_value=average_activation_value,
            total_nodes=total_nodes,
            low_activation_nodes=low_activation_nodes,
            duration_seconds=duration_seconds,
            trigger_type=trigger_type
        )
        
        db.add(history)
        db.commit()
        db.refresh(history)
        
        return history
    
    def get_recent_by_end_user(
        self,
        db: Session,
        end_user_id: str
    ) -> List[ForgettingCycleHistory]:
        """
        获取指定终端用户的所有历史记录（按时间降序排列）
        
        注意：此方法返回所有历史记录，调用方需要自行处理日期分组和数量限制。
        
        Args:
            db: 数据库会话
            end_user_id: 终端用户ID
        
        Returns:
            List[ForgettingCycleHistory]: 历史记录列表，按时间降序排列
        """
        return db.query(ForgettingCycleHistory).filter(
            ForgettingCycleHistory.end_user_id == end_user_id
        ).order_by(ForgettingCycleHistory.execution_time.desc()).all()
    
    def get_latest_by_end_user(
        self,
        db: Session,
        end_user_id: str
    ) -> Optional[ForgettingCycleHistory]:
        """
        获取指定终端用户的最新历史记录
        
        Args:
            db: 数据库会话
            end_user_id: 终端用户ID
        
        Returns:
            Optional[ForgettingCycleHistory]: 最新历史记录
        """
        return db.query(ForgettingCycleHistory).filter(
            ForgettingCycleHistory.end_user_id == end_user_id
        ).order_by(desc(ForgettingCycleHistory.execution_time)).first()
