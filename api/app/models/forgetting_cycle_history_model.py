"""
遗忘周期历史记录模型

用于存储每次遗忘周期执行的历史数据，支持趋势分析和可视化。
"""

import uuid
from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, Index
from sqlalchemy.dialects.postgresql import UUID
from app.db import Base


class ForgettingCycleHistory(Base):
    """遗忘周期历史记录表"""
    
    __tablename__ = "forgetting_cycle_history"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False, index=True, comment="主键ID")
    end_user_id = Column(String(255), nullable=False, comment="终端用户ID")
    execution_time = Column(DateTime, nullable=False, default=datetime.now, comment="执行时间")
    merged_count = Column(Integer, default=0, comment="本次成功融合的节点对数")
    failed_count = Column(Integer, default=0, comment="本次融合失败的节点对数")
    average_activation_value = Column(Float, nullable=True, comment="平均激活值")
    total_nodes = Column(Integer, default=0, comment="总节点数")
    low_activation_nodes = Column(Integer, default=0, comment="低于遗忘阈值的节点总数（包含已融合、失败和待处理的）")
    duration_seconds = Column(Float, nullable=True, comment="执行耗时（秒）")
    trigger_type = Column(String(50), default="manual", comment="触发类型: manual/scheduled")
    
    # 创建索引以优化查询
    __table_args__ = (
        Index('idx_end_user_time', 'end_user_id', 'execution_time'),
        Index('idx_execution_time', 'execution_time'),
    )
    
    def __repr__(self):
        return (
            f"<ForgettingCycleHistory(id={self.id}, end_user_id={self.end_user_id}, "
            f"merged_count={self.merged_count}, execution_time={self.execution_time})>"
        )
