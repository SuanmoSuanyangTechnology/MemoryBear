from __future__ import annotations

import asyncio
import uuid

from app.core.memory.storage.service import get_storage_service
from app.db import get_async_db_context
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.end_user_repository import EndUserRepository
from app.repositories.forget_log_repository import ForgetLogRepository
from app.repositories.memory_message_repository import MemoryMessageRepository
from app.repositories.memory_perceptual_repository import MemoryPerceptualRepository
from app.repositories.memory_short_repository import ShortTermMemoryRepository

WORKSPACE_STATISTICS_BATCH_SIZE = 1000
MEMORY_TYPE_ORDER = (
    "PERCEPTUAL_MEMORY",
    "WORKING_MEMORY",
    "SHORT_TERM_MEMORY",
    "EXPLICIT_MEMORY",
    "IMPLICIT_MEMORY",
    "EMOTIONAL_MEMORY",
    "EPISODIC_MEMORY",
    "FORGET_MEMORY",
)


async def _get_postgresql_statistics(
    end_user_ids: list[uuid.UUID],
) -> dict[str, int]:
    async with asyncio.TaskGroup() as task_group:
        perceptual_task = task_group.create_task(
            _get_perceptual_count(end_user_ids)
        )
        active_conversation_task = task_group.create_task(
            _get_active_conversation_count(end_user_ids)
        )
        api_mcp_source_task = task_group.create_task(
            _get_api_mcp_source_count(end_user_ids)
        )
        short_term_task = task_group.create_task(
            _get_short_term_count(end_user_ids)
        )
        pending_conversation_task = task_group.create_task(
            _get_pending_conversation_count(end_user_ids)
        )
        forget_task = task_group.create_task(
            _get_forget_count(end_user_ids)
        )

    return {
        "perceptual_count": perceptual_task.result(),
        "working_count": (
            active_conversation_task.result() + api_mcp_source_task.result()
        ),
        "short_term_count": (
            short_term_task.result() + pending_conversation_task.result()
        ),
        "forget_count": forget_task.result(),
    }


async def _get_perceptual_count(end_user_ids: list[uuid.UUID]) -> int:
    async with get_async_db_context() as db:
        return await MemoryPerceptualRepository(
            db
        ).get_count_by_user_ids_async(end_user_ids)


async def _get_active_conversation_count(
    end_user_ids: list[uuid.UUID],
) -> int:
    async with get_async_db_context() as db:
        return await ConversationRepository(
            db
        ).get_active_conversation_count_by_user_ids_async(end_user_ids)


async def _get_api_mcp_source_count(end_user_ids: list[uuid.UUID]) -> int:
    async with get_async_db_context() as db:
        return await MemoryMessageRepository(
            db
        ).get_working_memory_source_count_by_user_ids_async(end_user_ids)


async def _get_short_term_count(end_user_ids: list[uuid.UUID]) -> int:
    async with get_async_db_context() as db:
        return await ShortTermMemoryRepository(db).count_by_user_ids_async(
            end_user_ids
        )


async def _get_pending_conversation_count(
    end_user_ids: list[uuid.UUID],
) -> int:
    async with get_async_db_context() as db:
        return await ConversationRepository(
            db,
        ).get_pending_write_conversation_count_by_user_ids_async(end_user_ids)


async def _get_forget_count(end_user_ids: list[uuid.UUID]) -> int:
    async with get_async_db_context() as db:
        return await ForgetLogRepository.get_total_by_user_ids(
            db, end_user_ids
        )


async def _get_active_end_user_ids_page(
    workspace_id: uuid.UUID,
    after_id: uuid.UUID | None,
) -> list[uuid.UUID]:
    async with get_async_db_context() as db:
        return await EndUserRepository(db).get_active_end_user_ids_page_async(
            workspace_id,
            after_id=after_id,
            limit=WORKSPACE_STATISTICS_BATCH_SIZE,
        )


async def get_workspace_statistics_async(
    workspace_id: uuid.UUID,
) -> dict:
    """汇总 API Key workspace 下所有活跃终端用户的八类记忆。"""
    totals = {memory_type: 0 for memory_type in MEMORY_TYPE_ORDER}
    total_users = 0
    after_id: uuid.UUID | None = None

    while True:
        end_user_ids = await _get_active_end_user_ids_page(
            workspace_id,
            after_id,
        )
        if not end_user_ids:
            break

        storage_service = get_storage_service()
        postgresql_statistics, graph_statistics = await asyncio.gather(
            _get_postgresql_statistics(end_user_ids),
            storage_service.get_workspace_memory_graph_statistics(
                [str(end_user_id) for end_user_id in end_user_ids],
            ),
        )

        totals["PERCEPTUAL_MEMORY"] += postgresql_statistics[
            "perceptual_count"
        ]
        totals["WORKING_MEMORY"] += postgresql_statistics["working_count"]
        totals["SHORT_TERM_MEMORY"] += postgresql_statistics[
            "short_term_count"
        ]
        totals["EXPLICIT_MEMORY"] += graph_statistics["explicit_count"]
        totals["IMPLICIT_MEMORY"] += graph_statistics["implicit_count"]
        totals["EMOTIONAL_MEMORY"] += graph_statistics["emotional_count"]
        totals["EPISODIC_MEMORY"] += graph_statistics["episodic_count"]
        totals["FORGET_MEMORY"] += postgresql_statistics["forget_count"]
        total_users += len(end_user_ids)

        if len(end_user_ids) < WORKSPACE_STATISTICS_BATCH_SIZE:
            break
        after_id = end_user_ids[-1]

    total_count = sum(totals.values())
    statistics = [
        {
            "type": memory_type,
            "count": totals[memory_type],
            "percentage": (
                round(totals[memory_type] / total_count * 100, 2)
                if total_count > 0
                else 0.0
            ),
        }
        for memory_type in MEMORY_TYPE_ORDER
    ]
    return {
        "statistics": statistics,
        "total_count": total_count,
        "total_users": total_users,
    }
