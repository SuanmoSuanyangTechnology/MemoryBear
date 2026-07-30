"""记忆展示记录 Service

负责：
- 空类型过滤（memory_type 为空时不落 PG）
- 标题兜底
- 快照组装
- 异常隔离（PG 失败不影响主写入流程）
- 有限重试
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import List

from app.core.utils.datetime_utils import utcnow

logger = logging.getLogger(__name__)

# 最大重试次数（当前 Service 调用内）
_MAX_RETRIES = 2


class MemoryDisplayRecordService:
    """记忆展示记录业务逻辑层"""

    @staticmethod
    async def save_written(
        summaries: list,
        end_user_id: str,
    ) -> None:
        """将成功写入 Neo4j 的 MemorySummary 同步保存为 PG 展示记录。

        在 PG 写入前生成一个 operation_id，同批所有 Summary 共用该值。
        校验 memory_type 非空后批量写入。

        Args:
            summaries: 成功写入 Neo4j 的 MemorySummaryNode 列表
            end_user_id: 终端用户 ID
        """
        if not summaries:
            return

        from app.db import get_db_context
        from app.models.memory_display_record_model import MemoryDisplayRecord
        from app.repositories.memory_display_record_repository import (
            MemoryDisplayRecordRepository,
        )

        # 过滤 memory_type 为空的 summary
        valid_summaries = [
            s for s in summaries
            if s.memory_type and str(s.memory_type).strip()
        ]

        if not valid_summaries:
            logger.debug(
                "[MemoryDisplayRecord] 所有 Summary 的 memory_type 为空，跳过 PG 写入"
            )
            return

        # 批内按 memory_id 去重，保留首次出现（dict 保序）。
        # 唯一约束 uq_memory_display_records_user_op_memory 配合
        # ON CONFLICT DO NOTHING 负责重试幂等；这里只是避免同一条
        # INSERT 携带重复行，并让日志中的写入条数反映真实记录数。
        dedup_map = {}
        for s in valid_summaries:
            dedup_map.setdefault(s.id, s)
        deduped = list(dedup_map.values())

        # 生成 operation_id（同批共用）
        operation_id = uuid.uuid4()
        now = utcnow()

        # 组装 PG 记录
        records = []
        for s in deduped:
            # 解析 created_at
            created_at = _parse_created_at(s.created_at)

            # 标题兜底
            name = s.name if s.name and str(s.name).strip() else f"记忆_{s.id[:8]}"

            record = MemoryDisplayRecord(
                id=uuid.uuid4(),
                end_user_id=end_user_id,
                operation_id=operation_id,
                operation="WRITE",
                memory_id=s.id,
                memory_type=str(s.memory_type).strip(),
                name=str(name).strip(),
                content=s.content or "",
                created_at=created_at,
                score=None,
                rank=None,
                search_mode=None,
                occurred_at=now,
            )
            records.append(record)

        # 有限重试写入 PG
        last_error = None
        for attempt in range(_MAX_RETRIES):
            try:
                with get_db_context() as db:
                    repo = MemoryDisplayRecordRepository(db)
                    repo.bulk_insert_written(records)
                logger.info(
                    f"[MemoryDisplayRecord] PG 写入成功: "
                    f"end_user_id={end_user_id}, operation_id={operation_id}, "
                    f"count={len(records)}"
                )
                return
            except Exception as e:
                last_error = e
                logger.warning(
                    f"[MemoryDisplayRecord] PG 写入失败 (attempt {attempt + 1}/{_MAX_RETRIES}): {e}",
                    exc_info=True,
                )

        # 所有重试耗尽，只记录错误，不抛出异常
        logger.error(
            f"[MemoryDisplayRecord] PG 写入在 {_MAX_RETRIES} 次尝试后仍失败: "
            f"end_user_id={end_user_id}, operation_id={operation_id}, "
            f"error={last_error}"
        )


def _parse_created_at(value) -> datetime:
    """解析 MemorySummaryNode.created_at 为 UTC datetime。

    处理以下情况：
    - Python datetime（aware 或 naive）
    - ISO 8601 UTC 字符串（如 "2026-07-29T06:00:00Z"）
    - Neo4j DateTime 对象（有 to_native() 方法）
    """
    if value is None:
        return utcnow()

    # 已经是 datetime
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    # Neo4j DateTime 对象
    if hasattr(value, "to_native"):
        native = value.to_native()
        if native.tzinfo is None:
            return native.replace(tzinfo=timezone.utc)
        return native.astimezone(timezone.utc)

    # ISO 8601 字符串
    if isinstance(value, str):
        # 处理 Z 后缀
        s = value.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            logger.warning(f"[MemoryDisplayRecord] 无法解析 created_at: {value}")
            return utcnow()

    # 兜底
    logger.warning(f"[MemoryDisplayRecord] 未知 created_at 类型: {type(value)}")
    return utcnow()
