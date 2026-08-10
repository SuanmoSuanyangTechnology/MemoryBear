"""记忆展示记录 Repository

事务批量插入和按操作分页查询。
"""

import logging
import uuid
from typing import List, Tuple

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.end_user_model import EndUser
from app.models.memory_display_record_model import MemoryDisplayRecord

logger = logging.getLogger(__name__)


class MemoryDisplayRecordRepository:
    """记忆展示记录数据访问层"""

    def __init__(self, db: Session | AsyncSession):
        self.db = db

    def bulk_insert_written(
        self,
        records: List[MemoryDisplayRecord],
    ) -> int:
        """事务批量插入写入展示记录。

        使用 ON CONFLICT DO NOTHING 实现幂等写入，
        避免重试场景下的唯一约束冲突。

        Args:
            records: 组装好的 MemoryDisplayRecord 列表（已去重 memory_id）

        Returns:
            实际插入的行数
        """
        if not records:
            return 0

        values = []
        for r in records:
            values.append({
                "id": r.id,
                "end_user_id": r.end_user_id,
                "operation_id": r.operation_id,
                "operation": r.operation,
                "memory_id": r.memory_id,
                "memory_type": r.memory_type,
                "name": r.name,
                "content": r.content,
                "score": r.score,
                "rank": r.rank,
                "search_mode": r.search_mode,
                "occurred_at": r.occurred_at,
            })

        stmt = pg_insert(MemoryDisplayRecord).values(values)
        stmt = stmt.on_conflict_do_nothing(
            constraint="uq_memory_display_records_user_op_memory"
        )

        result = self.db.execute(stmt)
        self.db.commit()

        inserted = result.rowcount
        logger.info(
            f"[MemoryDisplayRecord] 批量插入完成: "
            f"attempted={len(records)}, inserted={inserted}"
        )
        return inserted

    def query_written_paginated(
        self,
        end_user_id: uuid.UUID,
        workspace_id: uuid.UUID,
        page: int,
        pagesize: int,
    ) -> Tuple[List[MemoryDisplayRecord], int]:
        """按 occurred_at DESC, id DESC 分页查询写入展示记录。

        查询附加 end_users 归属条件并限定 workspace_id，
        防止跨工作空间越权读取。

        Args:
            end_user_id: 终端用户 ID
            workspace_id: 当前工作空间 ID，用于数据隔离
            page: 页码（从 1 开始）
            pagesize: 每页数量

        Returns:
            (记录列表, 总条数)
        """
        owned_by_workspace = (
            select(EndUser.id)
            .where(
                EndUser.id == end_user_id,
                EndUser.workspace_id == workspace_id,
                EndUser.is_active.is_(True),
            )
            .exists()
        )

        base_filter = (
            (MemoryDisplayRecord.end_user_id == end_user_id)
            & (MemoryDisplayRecord.operation == "WRITE")
            & owned_by_workspace
        )

        total = (
            self.db.query(func.count(MemoryDisplayRecord.id))
            .filter(base_filter)
            .scalar()
        ) or 0

        offset = (page - 1) * pagesize
        items = (
            self.db.query(MemoryDisplayRecord)
            .filter(base_filter)
            .order_by(
                MemoryDisplayRecord.occurred_at.desc(),
                MemoryDisplayRecord.id.desc(),
            )
            .offset(offset)
            .limit(pagesize)
            .all()
        )

        return items, total

    async def query_written_paginated_async(
        self,
        end_user_id: uuid.UUID,
        workspace_id: uuid.UUID,
        page: int,
        pagesize: int,
    ) -> Tuple[List[MemoryDisplayRecord], int]:
        """异步按 occurred_at DESC, id DESC 分页查询写入展示记录。"""
        owned_by_workspace = (
            select(EndUser.id)
            .where(
                EndUser.id == end_user_id,
                EndUser.workspace_id == workspace_id,
                EndUser.is_active.is_(True),
            )
            .exists()
        )
        base_filter = (
            (MemoryDisplayRecord.end_user_id == end_user_id)
            & (MemoryDisplayRecord.operation == "WRITE")
            & owned_by_workspace
        )

        total_result = await self.db.execute(
            select(func.count(MemoryDisplayRecord.id)).where(base_filter)
        )
        total = total_result.scalar() or 0

        offset = (page - 1) * pagesize
        items_result = await self.db.execute(
            select(MemoryDisplayRecord)
            .where(base_filter)
            .order_by(
                MemoryDisplayRecord.occurred_at.desc(),
                MemoryDisplayRecord.id.desc(),
            )
            .offset(offset)
            .limit(pagesize)
        )
        return list(items_result.scalars().all()), total

    @staticmethod
    async def bulk_insert_retrieved_async(
        db: AsyncSession,
        rows: List[dict],
    ) -> int:
        """异步批量插入读取展示记录（operation = 'RETRIEVE'）。

        不指定冲突目标：主键或部分唯一索引
        ``uq_memory_display_retrieve_user_operation`` 任一冲突都视为已成功写入，
        consumer 重试因此天然幂等。

        Args:
            db: AsyncSession
            rows: 已聚合好的行（由 RetrieveDisplayTask.to_row() 生成）

        Returns:
            实际插入的行数
        """
        if not rows:
            return 0

        stmt = pg_insert(MemoryDisplayRecord).values(rows)
        stmt = stmt.on_conflict_do_nothing()

        result = await db.execute(stmt)
        await db.commit()
        return result.rowcount or 0

    def query_retrieved_paginated(
        self,
        end_user_id: uuid.UUID,
        page: int,
        pagesize: int,
    ) -> Tuple[List[MemoryDisplayRecord], int]:
        """按 occurred_at DESC, id DESC 分页查询读取展示记录。

        一次检索已经是一行，不再按 operation_id 分组或二次取明细。
        调用方负责校验终端用户的工作空间归属和可见性。

        Returns:
            (记录列表, 总条数)
        """
        base_filter = (
            (MemoryDisplayRecord.end_user_id == end_user_id)
            & (MemoryDisplayRecord.operation == "RETRIEVE")
        )

        total = (
            self.db.query(func.count(MemoryDisplayRecord.id))
            .filter(base_filter)
            .scalar()
        ) or 0

        offset = (page - 1) * pagesize
        items = (
            self.db.query(MemoryDisplayRecord)
            .filter(base_filter)
            .order_by(
                MemoryDisplayRecord.occurred_at.desc(),
                MemoryDisplayRecord.id.desc(),
            )
            .offset(offset)
            .limit(pagesize)
            .all()
        )

        return items, total

    async def query_retrieved_paginated_async(
        self,
        end_user_id: uuid.UUID,
        page: int,
        pagesize: int,
    ) -> Tuple[List[MemoryDisplayRecord], int]:
        """异步按 occurred_at DESC, id DESC 分页查询读取展示记录。"""
        base_filter = (
            (MemoryDisplayRecord.end_user_id == end_user_id)
            & (MemoryDisplayRecord.operation == "RETRIEVE")
        )

        total_result = await self.db.execute(
            select(func.count(MemoryDisplayRecord.id)).where(base_filter)
        )
        total = total_result.scalar() or 0

        offset = (page - 1) * pagesize
        items_result = await self.db.execute(
            select(MemoryDisplayRecord)
            .where(base_filter)
            .order_by(
                MemoryDisplayRecord.occurred_at.desc(),
                MemoryDisplayRecord.id.desc(),
            )
            .offset(offset)
            .limit(pagesize)
        )
        return list(items_result.scalars().all()), total
