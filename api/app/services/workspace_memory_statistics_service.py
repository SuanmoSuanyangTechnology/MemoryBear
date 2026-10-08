from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import suppress
from datetime import datetime, timedelta
from typing import Any

from app.cache.memory.workspace_statistics_cache import (
    WORKSPACE_STATISTICS_REFRESH_LOCK_RENEW_INTERVAL_SECONDS,
    WorkspaceStatisticsRefreshLockLostError,
    acquire_workspace_statistics_refresh_lock,
    add_workspace_statistics_snapshot,
    get_workspace_statistics_snapshots,
    release_workspace_statistics_refresh_lock,
    renew_workspace_statistics_refresh_lock,
)
from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.memory.storage.custom.workspace_statistics import (
    WorkspaceStatisticsStorage,
)
from app.core.memory.storage.provider.factory import BackendFactory
from app.core.utils.datetime_utils import (
    parse_iso_to_utc_naive,
    parse_timestamp_to_utc_naive,
    utcnow_naive,
)
from app.db import get_async_db_context
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.end_user_repository import EndUserRepository
from app.repositories.forget_log_repository import ForgetLogRepository
from app.repositories.memory_message_repository import MemoryMessageRepository
from app.repositories.memory_perceptual_repository import MemoryPerceptualRepository
from app.repositories.memory_short_repository import ShortTermMemoryRepository
from app.services.memory_base_service import MIN_MEMORY_SUMMARY_COUNT

logger = logging.getLogger(__name__)

WORKSPACE_STATISTICS_BATCH_SIZE = 1000
WORKSPACE_STATISTICS_REFRESH_POLL_INTERVAL_SECONDS = 0.5
WORKSPACE_STATISTICS_REFRESH_WAIT_TIMEOUT_SECONDS = 30.0
TODAY_SNAPSHOT_MAX_AGE = timedelta(hours=25)
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


class WorkspaceStatisticsRefreshTimeoutError(BusinessException):
    """等待同 workspace 的统计快照刷新超过接口时间预算。"""

    def __init__(self, workspace_id: uuid.UUID):
        super().__init__(
            "工作空间记忆统计正在生成，请稍后重试",
            BizCode.SERVICE_UNAVAILABLE,
            context={"workspace_id": str(workspace_id)},
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
    """汇总指定 workspace 下所有活跃终端用户的八类记忆。"""
    totals = {memory_type: 0 for memory_type in MEMORY_TYPE_ORDER}
    total_users = 0
    after_id: uuid.UUID | None = None

    factory: BackendFactory | None = None
    workspace_statistics_storage: WorkspaceStatisticsStorage | None = None
    try:
        while True:
            end_user_ids = await _get_active_end_user_ids_page(
                workspace_id,
                after_id,
            )
            if not end_user_ids:
                break

            if workspace_statistics_storage is None:
                factory = await BackendFactory.create()
                workspace_statistics_storage = WorkspaceStatisticsStorage(factory)

            async with asyncio.TaskGroup() as task_group:
                postgresql_task = task_group.create_task(
                    _get_postgresql_statistics(end_user_ids)
                )
                graph_task = task_group.create_task(
                    workspace_statistics_storage.get_statistics(
                        [str(end_user_id) for end_user_id in end_user_ids],
                        MIN_MEMORY_SUMMARY_COUNT,
                    )
                )
            postgresql_statistics = postgresql_task.result()
            graph_statistics = graph_task.result()

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
    finally:
        if factory is not None:
            try:
                await factory.close()
            except Exception:
                logger.exception(
                    "工作空间记忆统计后端客户端关闭失败: workspace_id=%s",
                    workspace_id,
                )

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


def _zero_statistics_snapshot() -> dict[str, Any]:
    return {
        "statistics": [
            {"type": memory_type, "count": 0, "percentage": 0.0}
            for memory_type in MEMORY_TYPE_ORDER
        ],
        "total_count": 0,
        "total_users": 0,
        "generated_at": None,
    }


def _snapshot_age(snapshot: dict[str, Any], now: datetime) -> timedelta:
    generated_at = snapshot.get("generated_at")
    if isinstance(generated_at, bool):
        raise ValueError("Workspace statistics snapshot has invalid generated_at")
    if isinstance(generated_at, (int, float)):
        generated_at_datetime = parse_timestamp_to_utc_naive(generated_at)
    elif isinstance(generated_at, str):
        # Redis snapshots written before v0.4.9 used ISO 8601 strings.
        generated_at_datetime = parse_iso_to_utc_naive(generated_at)
    else:
        generated_at_datetime = None
    if generated_at_datetime is None:
        raise ValueError("Workspace statistics snapshot has no generated_at")
    return now - generated_at_datetime


def _snapshot_version(snapshots: list[dict[str, Any]]) -> int | str | None:
    if not snapshots:
        return None
    generated_at = snapshots[0].get("generated_at")
    if isinstance(generated_at, bool):
        raise ValueError("Workspace statistics snapshot has invalid generated_at")
    if isinstance(generated_at, int):
        return generated_at
    if isinstance(generated_at, str) and generated_at:
        # Preserve version comparisons for unexpired legacy ISO snapshots.
        return generated_at
    raise ValueError("Workspace statistics snapshot has no generated_at")


async def get_workspace_statistics_snapshot_version_async(
    workspace_id: uuid.UUID,
) -> int | str | None:
    """读取当前最新快照版本，供跨 Celery 重试保持同一基线。"""
    snapshots = await get_workspace_statistics_snapshots(workspace_id)
    return _snapshot_version(snapshots)


def _fresh_cached_response(
    snapshots: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """只有两份且 today 未过期时可在首次读取后直接返回。"""
    if len(snapshots) != 2:
        return None

    now = utcnow_naive()
    today, yesterday = snapshots
    if _snapshot_age(today, now) > TODAY_SNAPSHOT_MAX_AGE:
        return None
    return {**today, "yesterday": yesterday}


def _response_from_completed_refresh(
    snapshots: list[dict[str, Any]],
) -> dict[str, Any]:
    """组装本轮已完成刷新后的结果，不再次套用一份快照刷新规则。"""
    if not snapshots:
        raise ValueError("Workspace statistics refresh produced no snapshot")
    yesterday = snapshots[1] if len(snapshots) > 1 else _zero_statistics_snapshot()
    return {**snapshots[0], "yesterday": yesterday}


async def _renew_refresh_lock(
    workspace_id: uuid.UUID,
    token: str,
) -> None:
    while True:
        await asyncio.sleep(
            WORKSPACE_STATISTICS_REFRESH_LOCK_RENEW_INTERVAL_SECONDS
        )
        try:
            renewed = await renew_workspace_statistics_refresh_lock(
                workspace_id,
                token,
            )
        except Exception as exc:
            logger.exception(
                "工作空间记忆统计刷新锁续租失败: workspace_id=%s",
                workspace_id,
            )
            raise WorkspaceStatisticsRefreshLockLostError(
                f"Workspace statistics refresh lock renewal failed: "
                f"workspace_id={workspace_id}"
            ) from exc
        if not renewed:
            logger.warning(
                "工作空间记忆统计刷新锁已失去所有权: workspace_id=%s",
                workspace_id,
            )
            raise WorkspaceStatisticsRefreshLockLostError(
                f"Workspace statistics refresh lock lost: "
                f"workspace_id={workspace_id}"
            )


async def _calculate_while_lease_is_valid(
    workspace_id: uuid.UUID,
    renewal_task: asyncio.Task[None],
) -> dict:
    calculation_task = asyncio.create_task(
        get_workspace_statistics_async(workspace_id)
    )
    done, _ = await asyncio.wait(
        {calculation_task, renewal_task},
        return_when=asyncio.FIRST_COMPLETED,
    )
    if renewal_task in done:
        calculation_task.cancel()
        with suppress(asyncio.CancelledError):
            await calculation_task
        await renewal_task
        raise WorkspaceStatisticsRefreshLockLostError(
            f"Workspace statistics refresh lock stopped: "
            f"workspace_id={workspace_id}"
        )
    return calculation_task.result()


async def _refresh_under_lock(
    workspace_id: uuid.UUID,
    token: str,
    initial_version: int | str | None,
) -> tuple[dict[str, Any], bool]:
    renewal_task = asyncio.create_task(_renew_refresh_lock(workspace_id, token))
    try:
        snapshots = await get_workspace_statistics_snapshots(workspace_id)
        if renewal_task.done():
            await renewal_task

        current_version = _snapshot_version(snapshots)
        if snapshots and current_version != initial_version:
            return _response_from_completed_refresh(snapshots), False

        statistics = await _calculate_while_lease_is_valid(
            workspace_id,
            renewal_task,
        )
        await add_workspace_statistics_snapshot(
            workspace_id,
            statistics,
            token,
        )
        refreshed_snapshots = await get_workspace_statistics_snapshots(workspace_id)
        return _response_from_completed_refresh(refreshed_snapshots), True
    finally:
        renewal_task.cancel()
        with suppress(
            asyncio.CancelledError,
            WorkspaceStatisticsRefreshLockLostError,
        ):
            await renewal_task
        try:
            await release_workspace_statistics_refresh_lock(workspace_id, token)
        except Exception:
            logger.exception(
                "工作空间记忆统计刷新锁释放失败: workspace_id=%s",
                workspace_id,
            )


async def get_or_refresh_workspace_statistics_async(
    workspace_id: uuid.UUID,
    *,
    wait_timeout_seconds: float = WORKSPACE_STATISTICS_REFRESH_WAIT_TIMEOUT_SECONDS,
    allow_cached_response: bool = True,
    baseline_version: int | str | None = None,
    baseline_captured: bool = False,
) -> tuple[dict[str, Any], bool]:
    """读取或串行刷新快照，返回响应数据及本调用是否实际写入。"""
    snapshots = await get_workspace_statistics_snapshots(workspace_id)
    cached_response = _fresh_cached_response(snapshots)
    if allow_cached_response and cached_response is not None:
        return cached_response, False

    observed_version = _snapshot_version(snapshots)
    initial_version = baseline_version if baseline_captured else observed_version
    if snapshots and baseline_captured and observed_version != initial_version:
        return _response_from_completed_refresh(snapshots), False

    token = uuid.uuid4().hex
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait_timeout_seconds

    if await acquire_workspace_statistics_refresh_lock(workspace_id, token):
        return await _refresh_under_lock(
            workspace_id,
            token,
            initial_version,
        )

    while loop.time() < deadline:
        remaining = deadline - loop.time()
        await asyncio.sleep(
            min(WORKSPACE_STATISTICS_REFRESH_POLL_INTERVAL_SECONDS, remaining)
        )
        observed_snapshots = await get_workspace_statistics_snapshots(workspace_id)
        if (
            observed_snapshots
            and _snapshot_version(observed_snapshots) != initial_version
        ):
            return _response_from_completed_refresh(observed_snapshots), False

        if loop.time() >= deadline:
            break
        if await acquire_workspace_statistics_refresh_lock(workspace_id, token):
            return await _refresh_under_lock(
                workspace_id,
                token,
                initial_version,
            )

    final_snapshots = await get_workspace_statistics_snapshots(workspace_id)
    if final_snapshots and _snapshot_version(final_snapshots) != initial_version:
        return _response_from_completed_refresh(final_snapshots), False
    raise WorkspaceStatisticsRefreshTimeoutError(workspace_id)


def _calculate_yesterday_change(today_count: int, yesterday_count: int) -> float | None:
    if yesterday_count == 0:
        return None
    return round((today_count - yesterday_count) / yesterday_count, 4)


def _with_yesterday_changes(response: dict[str, Any]) -> dict[str, Any]:
    yesterday = response["yesterday"]
    yesterday_counts = {
        item["type"]: item["count"] for item in yesterday["statistics"]
    }
    statistics = [
        {
            **item,
            "change": _calculate_yesterday_change(
                item["count"], yesterday_counts.get(item["type"], 0)
            ),
        }
        for item in response["statistics"]
    ]
    result = {
        **response,
        "statistics": statistics,
        "total_count_change": _calculate_yesterday_change(
            response["total_count"], yesterday["total_count"]
        ),
    }
    result.pop("yesterday")
    return result


async def get_cached_workspace_statistics_async(
    workspace_id: uuid.UUID,
) -> dict[str, Any]:
    """读取或生成快照，并返回 today 相对 yesterday 的变化比例。"""
    try:
        response, _ = await get_or_refresh_workspace_statistics_async(
            workspace_id
        )
    except WorkspaceStatisticsRefreshLockLostError as exc:
        raise BusinessException(
            "工作空间记忆统计正在生成，请稍后重试",
            BizCode.SERVICE_UNAVAILABLE,
            context={"workspace_id": str(workspace_id)},
            cause=exc,
        ) from exc
    return _with_yesterday_changes(response)
