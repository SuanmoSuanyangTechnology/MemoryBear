from datetime import datetime
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from typing import List, Optional, Tuple
import uuid

from app.core.utils.datetime_utils import utcnow_naive
from app.models.memory_increment_model import MemoryIncrement

from app.core.logging_config import get_db_logger

# 获取数据库专用日志器
db_logger = get_db_logger()


class MemoryIncrementRepository:
    def __init__(self, db: Session):
        self.db = db

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

    async def get_daily_latest_by_workspace_async(
        self,
        workspace_id: uuid.UUID,
        start_dt: datetime,
        end_dt: datetime,
    ) -> List[Tuple]:
        """按天取「当日 created_at 最晚的一条」total_num（created_at 闭区间过滤）。

        用 PostgreSQL 的 DISTINCT ON (created_at::date) 在库侧完成切日去重；
        同日多条取 created_at 最晚（时刻相同则 id 更大）的一条作为当天代表。

        Args:
            workspace_id: 工作空间 ID
            start_dt: 起始时间（naive UTC，闭区间）
            end_dt: 结束时间（naive UTC，闭区间）

        Returns:
            List[Tuple]: 每项为 (day: date, total_num: int)，按日期升序
        """
        day_expr = func.date(MemoryIncrement.created_at)
        stmt = (
            select(day_expr.label("day"), MemoryIncrement.total_num)
            .where(
                MemoryIncrement.workspace_id == workspace_id,
                MemoryIncrement.created_at >= start_dt,
                MemoryIncrement.created_at <= end_dt,
            )
            .distinct(day_expr)
            .order_by(
                day_expr.asc(),
                MemoryIncrement.created_at.desc(),
                MemoryIncrement.id.desc(),
            )
        )
        try:
            result = await self.db.execute(stmt)
            return [(row.day, row.total_num) for row in result.all()]
        except Exception as e:
            await self.db.rollback()
            db_logger.error(
                f"按日查询工作空间 {workspace_id} 记忆增量时出错: {str(e)}"
            )
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
                created_at=utcnow_naive(),
                updated_at=utcnow_naive()
            )
            self.db.add(memory_increment)
            self.db.commit()
            self.db.refresh(memory_increment)
            db_logger.info(f"成功写入内存增量: workspace_id={workspace_id}, total_num={total_num}")
            return memory_increment
        except Exception as e:
            db_logger.error(f"写入内存增量失败: workspace_id={workspace_id}, total_num={total_num} - {str(e)}")
            raise


async def write_memory_increment(
    db: AsyncSession, 
    workspace_id: uuid.UUID, 
    total_num: int
) -> MemoryIncrement:
    """写入内存增量（异步版本）"""
    memory_increment = MemoryIncrement(
        workspace_id=workspace_id,
        total_num=total_num,
        created_at=utcnow_naive(),
        updated_at=utcnow_naive()
    )
    db.add(memory_increment)
    await db.commit()
    await db.refresh(memory_increment)
    db_logger.info(f"成功写入内存增量（异步）: workspace_id={workspace_id}, total_num={total_num}")
    return memory_increment

def get_latest_memory_increment_by_workspace_id(db: Session, workspace_id: uuid.UUID) -> Optional[MemoryIncrement]:
    """根据工作空间ID查询最新的内存增量记录"""
    repo = MemoryIncrementRepository(db)
    return repo.get_latest_memory_increment_by_workspace_id(workspace_id)
