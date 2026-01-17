from sqlalchemy import func
from sqlalchemy.orm import Session, aliased
from typing import List, Optional
import uuid
import datetime

from app.models.memory_increment_model import MemoryIncrement

from app.core.logging_config import get_db_logger

# 获取数据库专用日志器
db_logger = get_db_logger()


class MemoryIncrementRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_memory_increments_by_workspace_id(self, workspace_id: uuid.UUID, limit: int) -> List[MemoryIncrement]:
        """根据工作空间ID查询内存增量：通过 MemoryIncrement 关联查询 MemoryIncrement 列表"""
        try:
            # 使用窗口函数按日期分区并排序
            subquery = (
                self.db.query(
                    MemoryIncrement,
                    func.row_number().over(
                        partition_by=func.date(MemoryIncrement.created_at),  # 按日期分区
                        order_by=MemoryIncrement.created_at.desc()  # 按时间戳降序排序，取每天最新的
                    ).label('row_num')
                )
                .filter(MemoryIncrement.workspace_id == workspace_id)
                .subquery()
            )

            memory_increment_alias = aliased(MemoryIncrement, subquery)

            # 先按时间降序取最近的limit条记录
            memory_increments = (
                self.db.query(memory_increment_alias)
                .filter(subquery.c.row_num == 1)  # 只取每个日期的第一条（最新的）
                .order_by(memory_increment_alias.created_at.desc())  # 按时间戳降序排序，取最近的
                .limit(limit)
                .all()
            )
            
            # 反转列表，使其按时间升序排列（从旧到新）
            memory_increments.reverse()
            
            db_logger.info(f"成功查询工作空间 {workspace_id} 下的内存增量，返回最近 {len(memory_increments)} 条记录")
            return memory_increments
        except Exception as e:
            db_logger.error(f"查询工作空间 {workspace_id} 下内存增量时出错: {str(e)}")
            raise

    def get_latest_memory_increment_by_workspace_id(self, workspace_id: uuid.UUID) -> Optional[MemoryIncrement]:
        """根据工作空间ID查询最新的内存增量记录"""
        try:
            memory_increment = (
                self.db.query(MemoryIncrement)
                .filter(MemoryIncrement.workspace_id == workspace_id)
                .order_by(MemoryIncrement.created_at.desc(), MemoryIncrement.id.desc())
                .first()
            )
            if memory_increment:
                db_logger.info(f"成功查询工作空间 {workspace_id} 下的最新内存增量")
            else:
                db_logger.warning(f"未找到工作空间 {workspace_id} 下的内存增量记录")
            return memory_increment
        except Exception as e:
            db_logger.error(f"查询工作空间 {workspace_id} 下最新内存增量时出错: {str(e)}")
            raise

    def write_memory_increment(
        self, 
        workspace_id: uuid.UUID, 
        total_num: int
    ) -> MemoryIncrement:
        """写入内存增量"""
        try:
            memory_increment = MemoryIncrement(
                workspace_id=workspace_id,
                total_num=total_num,
                created_at=datetime.datetime.now(),
                updated_at=datetime.datetime.now()
            )
            self.db.add(memory_increment)
            self.db.commit()
            self.db.refresh(memory_increment)
            db_logger.info(f"成功写入内存增量: workspace_id={workspace_id}, total_num={total_num}")
            return memory_increment
        except Exception as e:
            db_logger.error(f"写入内存增量失败: workspace_id={workspace_id}, total_num={total_num} - {str(e)}")
            raise


def get_memory_increments_by_workspace_id(db: Session, workspace_id: uuid.UUID, limit: int) -> List[MemoryIncrement]:
    """根据工作空间ID查询内存增量（返回 MemoryIncrement ORM 列表）"""
    repo = MemoryIncrementRepository(db)
    memory_increments = repo.get_memory_increments_by_workspace_id(workspace_id, limit)
    return memory_increments

def write_memory_increment(
    db: Session, 
    workspace_id: uuid.UUID, 
    total_num: int
) -> MemoryIncrement:
    """写入内存增量"""
    repo = MemoryIncrementRepository(db)
    memory_increment = repo.write_memory_increment(workspace_id, total_num)
    return memory_increment

def get_latest_memory_increment_by_workspace_id(db: Session, workspace_id: uuid.UUID) -> Optional[MemoryIncrement]:
    """根据工作空间ID查询最新的内存增量记录"""
    repo = MemoryIncrementRepository(db)
    return repo.get_latest_memory_increment_by_workspace_id(workspace_id)