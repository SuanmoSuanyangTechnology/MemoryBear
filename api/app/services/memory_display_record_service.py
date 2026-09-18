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
from datetime import datetime
from typing import List

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils.datetime_utils import (
    as_utc_aware,
    convert_neo4j_datetime_to_python,
    parse_timestamp_to_utc_naive,
    to_timestamp_ms,
    utcnow_naive,
)
from app.repositories.end_user_repository import EndUserRepository
from app.repositories.memory_display_record_repository import (
    MemoryDisplayRecordRepository,
)

logger = logging.getLogger(__name__)

# 最大重试次数（当前 Service 调用内）
_MAX_RETRIES = 2

def _as_uuid(value: uuid.UUID | str | None) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def _as_naive_utc(value) -> datetime | None:
    parsed = convert_neo4j_datetime_to_python(value)
    aware = as_utc_aware(parsed)
    return aware.replace(tzinfo=None) if aware is not None else None


class MemoryDisplayRecordService:
    """记忆展示记录业务逻辑层"""

    @staticmethod
    async def query_written(
        db: AsyncSession,
        workspace_id: uuid.UUID,
        page: int,
        pagesize: int,
        end_user_id: uuid.UUID | None = None,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> tuple[List[dict], int] | None:
        """查询写入展示记录并组装前端 DTO。

        - 传 ``end_user_id``：用户级查询，先校验其归属当前工作空间；
        - 不传 ``end_user_id``：空间级查询，返回整个 workspace 的写入记录，
          DTO 额外携带 ``end_user_id`` 供调用方区分归属。

        start_time/end_time 为毫秒 UTC 时间戳，按 occurred_at 闭区间过滤。
        返回 None 表示终端用户不属于当前工作空间。
        """
        if end_user_id is not None: # end_user_id = None传入仓储层，进行空间级查询
            end_user_repo = EndUserRepository(db)
            if await end_user_repo.get_active_end_user_in_workspace_async(
                end_user_id,
                workspace_id,
            ) is None:
                return None

        repo = MemoryDisplayRecordRepository(db)
        records, total = await repo.query_written_paginated_async(
            workspace_id=workspace_id,
            page=page,
            pagesize=pagesize,
            end_user_id=end_user_id,
            start_time=parse_timestamp_to_utc_naive(start_time),
            end_time=parse_timestamp_to_utc_naive(end_time),
        )

        items = [
            {
                "id": str(record.id),
                "end_user_id": str(record.end_user_id),
                "memory_id": record.memory_id,
                "memory_type": record.memory_type,
                "name": record.name,
                "content": record.content,
                "occurred_at": to_timestamp_ms(record.occurred_at),
            }
            for record in records
        ]
        return items, total

    @staticmethod
    async def save_fast_dialogue(
        *,
        end_user_id: str,
        dialogue_id: str,
        content: str,
        occurred_at: datetime,
        workspace_id: uuid.UUID | str | None = None,
    ) -> bool:
        """保存已由 Neo4j 回查确认的 Fast Dialogue 活动。"""
        normalized_content = str(content or "")
        if not dialogue_id or not normalized_content.strip():
            logger.warning(
                "[MemoryDisplayRecord] 非法 Fast Dialogue 快照，跳过: "
                "end_user_id=%s, dialogue_id=%s",
                end_user_id,
                dialogue_id,
            )
            return False

        try:
            end_user_uuid = _as_uuid(end_user_id)
            workspace_uuid = _as_uuid(workspace_id)
        except (ValueError, AttributeError, TypeError):
            logger.warning(
                "[MemoryDisplayRecord] Fast Dialogue 用户或工作空间 UUID 非法: "
                "end_user_id=%s, workspace_id=%s",
                end_user_id,
                workspace_id,
            )
            return False

        normalized_occurred_at = _as_naive_utc(occurred_at)
        if end_user_uuid is None or normalized_occurred_at is None:
            return False

        from app.db import get_db_context
        from app.models.memory_display_record_model import MemoryDisplayRecord

        operation_id = uuid.uuid4()
        record = MemoryDisplayRecord(
            id=uuid.uuid4(),
            end_user_id=end_user_uuid,
            workspace_id=workspace_uuid,
            operation_id=operation_id,
            operation="WRITE",
            memory_id=dialogue_id,
            memory_type="dialogue",
            name="用户对话原文",
            content=normalized_content,
            score=None,
            rank=None,
            search_mode=None,
            query=None,
            occurred_at=normalized_occurred_at,
        )

        last_error = None
        for attempt in range(_MAX_RETRIES):
            try:
                with get_db_context() as db:
                    repo = MemoryDisplayRecordRepository(db)
                    inserted = repo.bulk_insert_written([record])
                    db.commit()
                logger.info(
                    "[MemoryDisplayRecord] Fast Dialogue 活动已确认: "
                    "end_user_id=%s, dialogue_id=%s, operation_id=%s, inserted=%s",
                    end_user_id,
                    dialogue_id,
                    operation_id,
                    inserted,
                )
                return True
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "[MemoryDisplayRecord] Fast Dialogue PG 写入失败 "
                    "(attempt %s/%s): %s",
                    attempt + 1,
                    _MAX_RETRIES,
                    exc,
                    exc_info=True,
                )

        logger.error(
            "[MemoryDisplayRecord] Fast Dialogue PG 写入重试耗尽: "
            "end_user_id=%s, dialogue_id=%s, error=%s",
            end_user_id,
            dialogue_id,
            last_error,
        )
        return False

    @staticmethod
    async def replace_dialogue_with_summaries(
        summaries: list,
        end_user_id: str,
        workspace_id: uuid.UUID | None = None,
    ) -> bool:
        """在同一事务中用已落图的 Summary 替换 Fast Dialogue 活动。"""
        if not summaries:
            return False

        try:
            end_user_uuid = _as_uuid(end_user_id)
            workspace_uuid = _as_uuid(workspace_id)
        except (ValueError, AttributeError, TypeError):
            logger.warning(
                f"[MemoryDisplayRecord] 无法将 end_user_id 转为 UUID: {end_user_id}"
            )
            return False

        from app.db import get_db_context
        from app.models.memory_display_record_model import MemoryDisplayRecord

        valid_summaries = [
            s for s in summaries
            if getattr(s, "memory_type", None)
            and str(s.memory_type).strip()
        ]

        if not valid_summaries:
            logger.debug(
                "[MemoryDisplayRecord] 所有 Summary 的 memory_type 为空，跳过 PG 写入"
            )
            return False

        dialog_ids = {
            str(getattr(summary, "dialog_id", "") or "").strip()
            for summary in valid_summaries
        }
        if len(dialog_ids) != 1:
            logger.error(
                "[MemoryDisplayRecord] Summary 批次 dialog_id 不一致，保留 Dialogue: %s",
                sorted(dialog_ids),
            )
            return False
        dialogue_id = next(iter(dialog_ids))
        if not dialogue_id:
            logger.error(
                "[MemoryDisplayRecord] Summary dialog_id 为空，保留 Dialogue",
            )
            return False

        if any(
            str(getattr(summary, "end_user_id", "")) != str(end_user_id)
            for summary in valid_summaries
        ):
            logger.error(
                "[MemoryDisplayRecord] Summary 批次包含其它用户，保留 Dialogue: "
                "end_user_id=%s, dialogue_id=%s",
                end_user_id,
                dialogue_id,
            )
            return False

        # 批内按 memory_id 去重，保留首次出现（dict 保序）。
        # 唯一约束 uq_memory_display_records_user_op_memory 配合
        # ON CONFLICT DO NOTHING 负责重试幂等；这里只是避免同一条
        # INSERT 携带重复行，并让日志中的写入条数反映真实记录数。
        dedup_map = {}
        for s in valid_summaries:
            dedup_map.setdefault(s.id, s)
        deduped = list(dedup_map.values())

        operation_id = uuid.uuid4()
        last_error = None
        for attempt in range(_MAX_RETRIES):
            try:
                with get_db_context() as db:
                    repo = MemoryDisplayRecordRepository(db)
                    repo.delete_fast_dialogues(
                        end_user_id=end_user_uuid,
                        dialogue_id=dialogue_id,
                    )
                    occurred_at = (
                        _as_naive_utc(getattr(deduped[0], "created_at", None))
                        or utcnow_naive()
                    )

                    records = []
                    for summary in deduped:
                        name = (
                            summary.name
                            if summary.name and str(summary.name).strip()
                            else f"记忆_{summary.id[:8]}"
                        )
                        records.append(
                            MemoryDisplayRecord(
                                id=uuid.uuid4(),
                                end_user_id=end_user_uuid,
                                workspace_id=workspace_uuid,
                                operation_id=operation_id,
                                operation="WRITE",
                                memory_id=summary.id,
                                memory_type=str(summary.memory_type).strip(),
                                name=str(name).strip(),
                                content=summary.content or "",
                                score=None,
                                rank=None,
                                search_mode=None,
                                query=None,
                                occurred_at=occurred_at,
                            )
                        )

                    inserted = repo.bulk_insert_written(records)
                    db.commit()
                logger.info(
                    "[MemoryDisplayRecord] Dialogue 已替换为 Summary: "
                    "end_user_id=%s, dialogue_id=%s, operation_id=%s, "
                    "count=%s, inserted=%s",
                    end_user_id,
                    dialogue_id,
                    operation_id,
                    len(records),
                    inserted,
                )
                return True
            except Exception as e:
                last_error = e
                logger.warning(
                    f"[MemoryDisplayRecord] PG 写入失败 (attempt {attempt + 1}/{_MAX_RETRIES}): {e}",
                    exc_info=True,
                )

        logger.error(
            "[MemoryDisplayRecord] Dialogue 替换重试耗尽，原活动保持不变: "
            "end_user_id=%s, dialogue_id=%s, operation_id=%s, error=%s",
            end_user_id,
            dialogue_id,
            operation_id,
            last_error,
        )
        return False

    @staticmethod
    async def save_written(
        summaries: list,
        end_user_id: str,
        workspace_id: uuid.UUID | None = None,
    ) -> bool:
        """兼容旧调用名；实际执行 Dialogue → Summary 事务替换。"""
        return await MemoryDisplayRecordService.replace_dialogue_with_summaries(
            summaries=summaries,
            end_user_id=end_user_id,
            workspace_id=workspace_id,
        )
