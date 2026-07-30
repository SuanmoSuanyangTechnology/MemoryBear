"""记忆展示记录 Repository

事务批量插入和按操作分页查询。
"""

import logging
from typing import List, Tuple

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.memory_display_record_model import MemoryDisplayRecord

logger = logging.getLogger(__name__)


class MemoryDisplayRecordRepository:
    """记忆展示记录数据访问层"""

    def __init__(self, db: Session):
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
                "created_at": r.created_at,
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
        end_user_id: str,
        page: int,
        pagesize: int,
    ) -> Tuple[List[MemoryDisplayRecord], int]:
        """按 created_at DESC, id DESC 分页查询写入展示记录。

        Args:
            end_user_id: 终端用户 ID
            page: 页码（从 1 开始）
            pagesize: 每页数量

        Returns:
            (记录列表, 总条数)
        """
        base_filter = (
            (MemoryDisplayRecord.end_user_id == end_user_id)
            & (MemoryDisplayRecord.operation == "WRITE")
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
                MemoryDisplayRecord.created_at.desc(),
                MemoryDisplayRecord.id.desc(),
            )
            .offset(offset)
            .limit(pagesize)
            .all()
        )

        return items, total
