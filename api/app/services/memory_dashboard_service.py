import uuid
from datetime import timedelta
from typing import List, Optional, Dict, Any

from fastapi import HTTPException
from sqlalchemy import desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.logging_config import get_business_logger
from app.core.memory.rag import stats as rag_stats
from app.core.utils.datetime_utils import utcnow_naive
from app.db import get_async_db_context
from app.integrations.profile.contracts import MemoryProfileAuthSource
from app.integrations.profile.proxy import forward_memory_profile_request
from app.integrations.profile.runtime import is_memory_profile_enabled
from app.models.end_user_model import EndUser, EndUser as EndUserModel
from app.models.memory_increment_model import MemoryIncrement
from app.models.user_model import User
from app.repositories import (
    memory_increment_repository,
    knowledge_repository
)
from app.schemas.end_user_schema import EndUser as EndUserSchema
from app.schemas.memory_increment_schema import MemoryIncrement as MemoryIncrementSchema
from app.utils.redis_cache import redis_cache

# 获取业务逻辑专用日志器
business_logger = get_business_logger()


def _workspace_end_user_ids(db: Session, workspace_id: uuid.UUID) -> List[str]:
    """工作空间下所有活跃宿主 ID（与 RAG 统计口径一致）。"""
    from app.repositories.end_user_repository import EndUserRepository

    return EndUserRepository(db).get_ids_by_app_workspace(workspace_id)


async def _workspace_end_user_ids_async(
    db: AsyncSession,
    workspace_id: uuid.UUID,
) -> List[str]:
    from app.repositories.end_user_repository import EndUserRepository

    return await EndUserRepository(db).get_ids_by_app_workspace_async(workspace_id)


def _is_rag_workspace(db: Session, workspace_id: uuid.UUID) -> bool:
    from app.models.workspace_model import Workspace

    row = db.query(Workspace.storage_type).filter(Workspace.id == workspace_id).first()
    return bool(row and row[0] == "rag")


async def _is_rag_workspace_async(
    db: AsyncSession,
    workspace_id: uuid.UUID,
) -> bool:
    from sqlalchemy import select

    from app.models.workspace_model import Workspace

    result = await db.execute(
        select(Workspace.storage_type).where(Workspace.id == workspace_id)
    )
    return result.scalar_one_or_none() == "rag"


async def get_dashboard_memory_updates_async(
    workspace_id: uuid.UUID,
    storage_type: str,
    current_total: int | None,
) -> dict[str, int | None]:
    """Reuse the branch total and compare it with workspace-level baselines."""
    from app.repositories.end_user_repository import EndUserRepository

    result = {"memory_update": None, "memory_update_change": None}
    if current_total is None:
        return result
    today_start = utcnow_naive().replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = today_start - timedelta(days=1)
    day_before_start = today_start - timedelta(days=2)

    try:
        # Keep failed metric queries isolated from the existing dashboard session.
        async with get_async_db_context() as db:
            if storage_type == "neo4j":
                rows = await memory_increment_repository.MemoryIncrementRepository(
                    db
                ).get_daily_latest_by_workspace_async(
                    workspace_id,
                    day_before_start,
                    today_start - timedelta(microseconds=1),
                )
                baselines = dict(rows)
                yesterday = baselines.get(yesterday_start.date())
                day_before = baselines.get(day_before_start.date())
            elif storage_type == "rag":
                users = EndUserRepository(db)
                user_ids = await users.get_ids_by_app_workspace_async(workspace_id)
                yesterday = await rag_stats.count_messages(
                    user_ids,
                    end=today_start - timedelta(microseconds=1),
                )
                day_before = await rag_stats.count_messages(
                    user_ids,
                    end=yesterday_start - timedelta(microseconds=1),
                )
            else:
                return result

        if current_total is not None and yesterday is not None:
            result["memory_update"] = int(current_total) - int(yesterday)
            if day_before is not None:
                result["memory_update_change"] = result["memory_update"] - (
                    int(yesterday) - int(day_before)
                )
    except Exception:
        business_logger.warning(
            "Dashboard memory update query failed: workspace_id=%s storage_type=%s",
            workspace_id,
            storage_type,
            exc_info=True,
        )
    return result


async def get_dashboard_workspace_statistics_async(
    workspace_id: uuid.UUID,
    *,
    current_user: Any,
    source: MemoryProfileAuthSource,
) -> dict[str, Any] | None:
    """Return the independent workspace statistics fields, or None on failure.

    When the profile integration is enabled this block is forwarded to
    memory-profile; the rest of the dashboard stays on its local implementation.
    A profile failure degrades this block to ``None`` (the dashboard already
    treats it as optional) instead of failing the whole aggregate.
    """
    try:
        if is_memory_profile_enabled():
            envelope = await forward_memory_profile_request(
                method="GET",
                path="/internal/v1/memory/analytics/workspace_statistics",
                current_user=current_user,
                source=source,
            )
            if not isinstance(envelope, dict) or envelope.get("code") != 0:
                raise RuntimeError("memory profile returned a business failure")
            statistics = envelope.get("data") or {}
        else:
            from app.services.workspace_memory_statistics_service import (
                get_cached_workspace_statistics_async,
            )

            statistics = await get_cached_workspace_statistics_async(workspace_id)
        return {
            field: statistics[field]
            for field in (
                "statistics", "total_count", "total_count_change", "total_users",
                "generated_at",
            )
        }
    except Exception:
        business_logger.warning(
            "Dashboard workspace statistics query failed: workspace_id=%s",
            workspace_id,
            exc_info=True,
        )
    return None


def get_current_workspace_type(
    db: Session,
    workspace_id: uuid.UUID,
    current_user: User
) -> Optional[str]:
    """获取当前工作空间类型"""
    business_logger.info(f"获取工作空间类型: workspace_id={workspace_id}, 操作者: {current_user.username}")

    try:
        from app.repositories.workspace_repository import get_workspace_by_id

        workspace = get_workspace_by_id(db, workspace_id)
        if not workspace:
            business_logger.warning(f"工作空间不存在: workspace_id={workspace_id}")
            return None

        business_logger.info(f"成功获取工作空间类型: {workspace.storage_type}")
        return workspace.storage_type

    except Exception as e:
        business_logger.error(f"获取工作空间类型失败: workspace_id={workspace_id} - {str(e)}")
        raise


async def get_workspace_end_users_async(
    db,
    workspace_id: uuid.UUID,
    current_user
) -> List[EndUser]:
    """获取工作空间的所有宿主（异步版本，使用 repository）"""
    business_logger.info(f"获取工作空间宿主列表(异步): workspace_id={workspace_id}, 操作者: {current_user.username}")

    try:
        from app.repositories.end_user_repository import EndUserRepository

        repo = EndUserRepository(db)
        end_users_orm = await repo.get_end_users_by_workspace_async(workspace_id)

        end_users = [EndUserSchema.model_validate(eu) for eu in end_users_orm]

        business_logger.info(f"成功获取 {len(end_users)} 个宿主记录")
        return end_users

    except HTTPException:
        raise
    except Exception as e:
        business_logger.error(f"获取工作空间宿主列表失败(异步): workspace_id={workspace_id} - {str(e)}")
        raise


def get_workspace_end_users_paginated(
    db: Session,
    workspace_id: uuid.UUID,
    current_user: User,
    page: int,
    pagesize: int,
    keyword: Optional[str] = None,
    label: Optional[str] = None,
) -> Dict[str, Any]:
    """获取工作空间的宿主列表（分页版本，支持模糊搜索）

    返回结果按 created_at 从新到旧排序（NULL 值排在最后）
    固定过滤 memory_count > 0 的宿主，保证分页基于“有记忆宿主”集合计算。
    支持通过 keyword 参数同时模糊搜索 other_name 和 id 字段

    Args:
        db: 数据库会话
        workspace_id: 工作空间ID
        current_user: 当前用户
        page: 页码（从1开始）
        pagesize: 每页数量
        keyword: 搜索关键词（可选，同时模糊匹配 other_name 和 id）

    Returns:
        dict: 包含 items（宿主列表）和 total（总记录数）的字典
    """
    business_logger.info(f"获取工作空间宿主列表（分页）: workspace_id={workspace_id}, keyword={keyword}, page={page}, pagesize={pagesize}, 操作者: {current_user.username}")

    try:
        from app.repositories.end_user_repository import EndUserRepository

        repo = EndUserRepository(db)
        end_users_orm, total = repo.get_paginated_with_memory(
            workspace_id=workspace_id,
            page=page,
            pagesize=pagesize,
            keyword=keyword,
            label=label,
        )

        business_logger.info(f"成功获取 {len(end_users_orm)} 个宿主记录，总计 {total} 条")
        return {"items": end_users_orm, "total": total}

    except HTTPException:
        raise
    except Exception as e:
        business_logger.error(f"获取工作空间宿主列表（分页）失败: workspace_id={workspace_id} - {str(e)}")
        raise

def get_workspace_end_users_paginated_rag(
    db: Session,
    workspace_id: uuid.UUID,
    current_user: User,
    page: int,
    pagesize: int,
    keyword: Optional[str] = None,
    label: Optional[str] = None,
) -> Dict[str, Any]:
    """RAG 模式宿主列表分页。

    RAG 记忆数量以独立索引 `rag_memory_current` 为准：
    - 通过 App 关联取当前 workspace 下所有活跃宿主，按宿主聚合消息条数
    - 只保留消息条数 > 0 的宿主，保证分页准确
    """
    business_logger.info(
        f"获取 RAG 宿主列表（分页）: workspace_id={workspace_id}, "
        f"keyword={keyword}, page={page}, pagesize={pagesize}, 操作者: {current_user.username}"
    )

    try:
        from app.repositories.end_user_repository import EndUserRepository

        repo = EndUserRepository(db)
        memory_counts = rag_stats.count_messages_by_end_user_sync(
            _workspace_end_user_ids(db, workspace_id)
        )
        items, total = repo.get_paginated_with_memory_rag(
            workspace_id=workspace_id,
            page=page,
            pagesize=pagesize,
            memory_counts=memory_counts,
            keyword=keyword,
            label=label,
        )

        business_logger.info(f"成功获取 RAG 宿主记录 {len(items)} 条，总计 {total} 条")
        return {"items": items, "total": total}

    except HTTPException:
        raise
    except Exception as e:
        business_logger.error(
            f"获取 RAG 宿主列表（分页）失败: workspace_id={workspace_id} - {str(e)}"
        )
        raise

async def get_workspace_api_increment_async(
    workspace_id: uuid.UUID,
    current_user,
) -> int:
    """获取工作空间的API调用增量（异步版本）"""
    business_logger.info(f"获取工作空间API调用增量: workspace_id={workspace_id}, 操作者: {current_user.username}")

    try:
        # 查询API调用增量
        api_increment = 856

        business_logger.info(f"成功获取 {api_increment} API调用增量")
        return api_increment

    except HTTPException:
        raise
    except Exception as e:
        business_logger.error(f"获取工作空间API调用增量失败: workspace_id={workspace_id} - {str(e)}")
        raise


async def _get_rag_memory_increment_async(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    limit: int,
) -> List[MemoryIncrementSchema]:
    """RAG 记忆增量：独立索引按 UTC 自然日聚合累计总量。

    与图侧 `memory_increments.total_num` 的语义一致（当日累计总量），
    迁移数据（source=legacy）不计入趋势，避免一次性尖峰。
    """
    from datetime import datetime, time

    end_user_ids = await _workspace_end_user_ids_async(db, workspace_id)
    today = utcnow_naive().replace(hour=0, minute=0, second=0, microsecond=0)
    start_day = today - timedelta(days=max(int(limit) - 1, 0))
    totals = await rag_stats.daily_totals(
        end_user_ids,
        start_day,
        today,
        exclude_legacy=True,
    )
    return [
        MemoryIncrementSchema(
            workspace_id=workspace_id,
            total_num=total,
            created_at=datetime.combine(day, time.min),
            updated_at=datetime.combine(day, time.min),
        )
        for day, total in totals
    ]


async def get_workspace_memory_increment_async(
    db,
    workspace_id: uuid.UUID,
    limit: int,
    current_user
) -> List[MemoryIncrementSchema]:
    """获取工作空间的记忆增量（异步版本）"""
    business_logger.info(f"获取记忆增量(异步): workspace_id={workspace_id}, limit={limit}, 操作者: {current_user.username}")
    from sqlalchemy import select, func

    if await _is_rag_workspace_async(db, workspace_id):
        return await _get_rag_memory_increment_async(db, workspace_id, limit)

    # 每天只保留一条（取当天最新），再取最新的 limit 天：
    # DISTINCT ON (date(created_at)) + ORDER BY date DESC, created_at DESC 保证
    # 同一天内返回的是 created_at 最新的一条。
    day_expr = func.date(MemoryIncrement.created_at)
    stmt = (
        select(MemoryIncrement)
        .where(MemoryIncrement.workspace_id == workspace_id)
        .distinct(day_expr)
        .order_by(day_expr.desc(), MemoryIncrement.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    records = result.scalars().all()

    # 再按时间升序输出（最旧在前）
    records.sort(key=lambda r: r.created_at)

    increments = [MemoryIncrementSchema.model_validate(r) for r in records]
    business_logger.info(f"成功获取 {len(increments)} 条记忆增量记录")
    return increments


async def get_workspace_memory_list_async(
    db,
    workspace_id: uuid.UUID,
    current_user,
    limit: int = 7
) -> dict:
    """获取工作空间的记忆列表（异步版本）"""
    business_logger.info(f"获取工作空间记忆列表(异步): workspace_id={workspace_id}, 操作者: {current_user.username}")

    result = {}

    try:
        # 1. 获取记忆总量
        try:
            if await _is_rag_workspace_async(db, workspace_id):
                result["total_memory"] = await rag_stats.count_messages(
                    await _workspace_end_user_ids_async(db, workspace_id)
                )
            else:
                total_memory_record = await memory_increment_repository.write_memory_increment(db, workspace_id, 0)
                result["total_memory"] = total_memory_record.total_num
            business_logger.info(f"成功获取记忆总量: {result['total_memory']}")
        except Exception as e:
            business_logger.warning(f"获取记忆总量失败: {str(e)}")
            result["total_memory"] = 0.0

        # 2. 获取记忆增量
        try:
            memory_increment = await get_workspace_memory_increment_async(db, workspace_id, limit, current_user)
            result["memory_increment"] = memory_increment
            business_logger.info(f"成功获取 {len(memory_increment)} 条记忆增量记录")
        except Exception as e:
            business_logger.warning(f"获取记忆增量失败: {str(e)}")
            result["memory_increment"] = []

        # 3. 获取宿主列表
        try:
            hosts = await get_workspace_end_users_async(db, workspace_id, current_user)
            result["hosts"] = hosts
            business_logger.info(f"成功获取 {len(hosts)} 个宿主记录")
        except Exception as e:
            business_logger.warning(f"获取宿主列表失败: {str(e)}")
            result["hosts"] = []

        business_logger.info("成功获取工作空间记忆列表")
        return result

    except HTTPException:
        raise
    except Exception as e:
        business_logger.error(f"获取工作空间记忆列表失败(异步): workspace_id={workspace_id} - {str(e)}")
        raise


async def get_workspace_total_end_users_async(
    db,
    workspace_id: uuid.UUID,
    current_user
) -> dict:
    """获取用户列表的总用户数（异步版本）"""
    business_logger.info(f"获取用户列表的总用户数(异步): workspace_id={workspace_id}, 操作者: {current_user.username}")

    try:
        end_users = await get_workspace_end_users_async(db, workspace_id, current_user)

        business_logger.info(f"成功获取 {len(end_users)} 个宿主记录")
        return {
            "total_num": len(end_users),
            "online_num": len(end_users)
        }

    except HTTPException:
        raise
    except Exception as e:
        business_logger.error(f"获取用户列表失败(异步): workspace_id={workspace_id} - {str(e)}")
        raise


async def get_workspace_total_memory_count_only_async(
    db,
    workspace_id: uuid.UUID,
    current_user,
    end_user_id: str = None
) -> int:
    """获取工作空间的记忆总量（只回传总量数值，不含宿主明细）。

    面向只需要一个总数的场景（如 /dashboard_data 的 total_memory）。全工作空间分支
    下推为单条 SUM/COUNT 聚合，不拉取任何宿主明细行，省掉"查出全部宿主 -> 逐行构造
    details -> 求和 -> 丢弃 details"的整条链路；宿主数上万时差别显著。

    与 get_workspace_total_memory_count_async 的数值口径完全一致，两者区别仅在于
    是否返回 details，故共用同一份 end_users.memory_count 数据源。缓存 prefix 独立，
    避免与返回 dict 的版本互相覆盖。

    RAG 空间改读独立索引（消息条数），且不经过缓存，保证写入后立即可见。

    Returns:
        int: 记忆总量；未找到指定宿主或无活跃宿主时为 0
    """
    business_logger.info(
        f"获取工作空间记忆总量(仅总量): workspace_id={workspace_id}, 操作者: {current_user.username}"
    )

    try:
        if await _is_rag_workspace_async(db, workspace_id):
            end_user_ids = (
                [end_user_id]
                if end_user_id
                else await _workspace_end_user_ids_async(db, workspace_id)
            )
            return await rag_stats.count_messages(end_user_ids)

        return await _get_workspace_total_memory_count_only_cached_async(
            db=db,
            workspace_id=workspace_id,
            current_user=current_user,
            end_user_id=end_user_id,
        )

    except HTTPException:
        raise
    except Exception as e:
        business_logger.error(f"获取工作空间记忆总量(仅总量)失败: workspace_id={workspace_id} - {str(e)}")
        raise


@redis_cache(prefix="memory_count_total", id_arg="workspace_id", skip_args=["current_user"])
async def _get_workspace_total_memory_count_only_cached_async(
    db,
    workspace_id: uuid.UUID,
    current_user,
    end_user_id: str = None
) -> int:
    """图侧记忆总量（读 end_users.memory_count，带缓存）。"""
    from app.repositories.end_user_repository import EndUserRepository

    repo = EndUserRepository(db)

    # 指定宿主：沿用单条查询，保住 get_end_user_by_id_async 的合并路由语义
    if end_user_id:
        end_user = await repo.get_end_user_by_id_async(uuid.UUID(end_user_id))
        if not end_user:
            business_logger.warning(f"未找到宿主 {end_user_id}，返回0")
            return 0
        total_count = int(end_user.memory_count or 0)
    else:
        # 全工作空间：聚合在 PG 内完成，只回传一行
        raw_total, _ = await repo.get_memory_count_total_by_workspace_async(workspace_id)
        total_count = int(raw_total or 0)

    business_logger.info(f"成功获取工作空间记忆总量(仅总量): {total_count}")
    return total_count


async def get_workspace_total_memory_count(
    db,
    workspace_id: uuid.UUID,
    current_user,
    end_user_id: str = None
) -> dict:
    """获取工作空间的记忆总量（保留旧函数名，委托给 _async 版本）。

    历史上此函数与 get_workspace_total_memory_count_async 是两份重复实现，
    均通过实时扫描 Neo4j 聚合。现统一改为读取 end_users.memory_count 字段，
    为避免逻辑漂移，这里直接委托，二者行为与响应结构完全一致。
    """
    return await get_workspace_total_memory_count_async(
        db=db,
        workspace_id=workspace_id,
        current_user=current_user,
        end_user_id=end_user_id,
    )


async def get_workspace_total_memory_count_async(
    db,
    workspace_id: uuid.UUID,
    current_user,
    end_user_id: str = None
) -> dict:
    """获取工作空间的记忆总量（异步版本，使用 AsyncSession）。

    - RAG 空间：独立索引按消息条数统计（不经过缓存，写入后立即可见）。
    - 图空间：直接读取 end_users.memory_count 字段聚合，不再实时扫描 Neo4j；
      memory_count 由写入/遗忘等流程维护，可能相对 Neo4j 实时计数有极小滞后，
      但换取的是数量级的耗时下降（全库扫描 -> 单条索引聚合）。
    """
    business_logger.info(f"获取工作空间记忆总量(异步): workspace_id={workspace_id}, 操作者: {current_user.username}")

    try:
        if await _is_rag_workspace_async(db, workspace_id):
            return await _get_rag_total_memory_count_async(db, workspace_id, end_user_id)

        return await _get_workspace_total_memory_count_cached_async(
            db=db,
            workspace_id=workspace_id,
            current_user=current_user,
            end_user_id=end_user_id,
        )

    except HTTPException:
        raise
    except Exception as e:
        business_logger.error(f"获取工作空间记忆总量失败(异步): workspace_id={workspace_id} - {str(e)}")
        raise


async def _get_rag_total_memory_count_async(
    db,
    workspace_id: uuid.UUID,
    end_user_id: str = None,
) -> dict:
    """RAG 空间记忆总量（含宿主明细），计数口径为消息条数。"""
    from app.repositories.end_user_repository import EndUserRepository

    repo = EndUserRepository(db)

    if end_user_id:
        end_user = await repo.get_end_user_by_id_async(uuid.UUID(end_user_id))
        if not end_user:
            business_logger.warning(f"未找到宿主 {end_user_id}，返回0")
            return {
                "total_memory_count": 0,
                "host_count": 0,
                "details": []
            }

        count = await rag_stats.count_messages([end_user_id])
        return {
            "total_memory_count": count,
            "host_count": 1,
            "details": [{
                "end_user_id": end_user_id,
                "count": count,
                "name": end_user.other_name or None
            }]
        }

    rows = await repo.get_memory_counts_by_workspace_async(workspace_id)
    if not rows:
        business_logger.warning("未找到任何宿主，返回0")
        return {
            "total_memory_count": 0,
            "host_count": 0,
            "details": []
        }

    counts = await rag_stats.count_messages_by_end_user(
        [str(row.id) for row in rows]
    )
    details = [
        {
            "end_user_id": str(row.id),
            "count": counts.get(str(row.id), 0),
            "name": row.other_name or None
        }
        for row in rows
    ]
    total_count = sum(item["count"] for item in details)

    business_logger.info(f"成功获取 RAG 记忆总量: {total_count} (来自 {len(rows)} 个宿主)")
    return {
        "total_memory_count": total_count,
        "host_count": len(rows),
        "details": details
    }


@redis_cache(prefix="memory_count", id_arg="workspace_id", skip_args=["current_user"])
async def _get_workspace_total_memory_count_cached_async(
    db,
    workspace_id: uuid.UUID,
    current_user,
    end_user_id: str = None
) -> dict:
    """图侧记忆总量（读 end_users.memory_count，带缓存）。"""
    from app.repositories.end_user_repository import EndUserRepository

    repo = EndUserRepository(db)

    # 指定宿主：只查该宿主的 memory_count（保留合并路由语义）
    if end_user_id:
        end_user = await repo.get_end_user_by_id_async(uuid.UUID(end_user_id))
        if not end_user:
            business_logger.warning(f"未找到宿主 {end_user_id}，返回0")
            return {
                "total_memory_count": 0,
                "host_count": 0,
                "details": []
            }

        count = int(end_user.memory_count or 0)
        return {
            "total_memory_count": count,
            "host_count": 1,
            "details": [{
                "end_user_id": end_user_id,
                "count": count,
                "name": end_user.other_name or None
            }]
        }

    # 全工作空间：一次轻量查询取所有活跃宿主的 (id, other_name, memory_count)
    rows = await repo.get_memory_counts_by_workspace_async(workspace_id)
    if not rows:
        business_logger.warning("未找到任何宿主，返回0")
        return {
            "total_memory_count": 0,
            "host_count": 0,
            "details": []
        }

    details = [
        {
            "end_user_id": str(row.id),
            "count": int(row.memory_count or 0),
            "name": row.other_name or None
        }
        for row in rows
    ]
    total_count = sum(item["count"] for item in details)

    result = {
        "total_memory_count": total_count,
        "host_count": len(rows),
        "details": details
    }

    business_logger.info(f"成功获取工作空间记忆总量: {total_count} (来自 {len(rows)} 个宿主)")
    return result


async def get_end_user_memory_counts_async(
    db,
    workspace_id: uuid.UUID,
    end_user_ids: List[uuid.UUID],
) -> Dict[str, Any]:
    """批量查询终端用户记忆量。

    - RAG 空间：按独立索引聚合消息条数。
    - 图空间：读 end_users.memory_count 字段。

    仅统计「属于目标空间且 is_active=True」的用户；不属于空间/已删除的 id 不返回
    （不报错）。id 的合法性与数量上限由 controller 层校验。

    Returns:
        {"total": int, "items": [{"end_user_id", "other_name", "memory_count"}]}
    """
    business_logger.info(
        f"批量查询终端用户记忆量: workspace_id={workspace_id}, count={len(end_user_ids)}"
    )
    from app.repositories.end_user_repository import EndUserRepository

    repo = EndUserRepository(db)
    rows = await repo.get_memory_counts_by_ids_async(workspace_id, end_user_ids)

    if await _is_rag_workspace_async(db, workspace_id):
        is_rag = True
        counts = await rag_stats.count_messages_by_end_user(
            [str(row.id) for row in rows]
        )
    else:
        is_rag = False
        counts = {}

    items = [
        {
            "end_user_id": str(row.id),
            "other_name": row.other_name or None,
            "memory_count": (
                counts.get(str(row.id), 0)
                if is_rag
                else int(row.memory_count or 0)
            ),
        }
        for row in rows
    ]
    total = sum(item["memory_count"] for item in items)

    business_logger.info(f"批量查询终端用户记忆量完成: total={total}, hit={len(items)}")
    return {"total": total, "items": items}


async def get_memory_increment_daily_async(
    db,
    workspace_id: uuid.UUID,
    start_ms: int,
    end_ms: int,
) -> Dict[str, Any]:
    """按时间段逐日返回记忆总量（同日取当日最新一条）。

    - 图空间：过滤 memory_increments.created_at（闭区间），同日取最新记录。
    - RAG 空间：独立索引按 UTC 自然日聚合累计总量，排除迁移数据（source=legacy）。
    start_ms/end_ms 为毫秒 UTC 时间戳；返回的 date 为代表日期的 UTC 当日
    00:00:00.000 毫秒时间戳。

    Returns:
        {"items": [{"date": int(ms), "total_num": int}]}
    """
    from datetime import datetime, timezone

    from app.core.utils.datetime_utils import parse_timestamp_to_utc_naive
    from app.repositories.memory_increment_repository import MemoryIncrementRepository

    # 毫秒 UTC → naive UTC datetime（DB 按项目约定存 naive UTC）
    start_dt = parse_timestamp_to_utc_naive(start_ms)
    end_dt = parse_timestamp_to_utc_naive(end_ms)

    business_logger.info(
        f"按日查询记忆增量: workspace_id={workspace_id}, start={start_dt}, end={end_dt}"
    )

    def _to_day_ms(day) -> int:
        return int(
            datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() * 1000
        )

    if await _is_rag_workspace_async(db, workspace_id):
        end_user_ids = await _workspace_end_user_ids_async(db, workspace_id)
        totals = await rag_stats.daily_totals(
            end_user_ids,
            start_dt,
            end_dt,
            exclude_legacy=True,
        )
        items = [
            {"date": _to_day_ms(day), "total_num": int(total)}
            for day, total in totals
        ]
        business_logger.info(f"按日查询 RAG 记忆增量完成: days={len(items)}")
        return {"items": items}

    repo = MemoryIncrementRepository(db)
    rows = await repo.get_daily_latest_by_workspace_async(workspace_id, start_dt, end_dt)

    items = []
    for day, total_num in rows:
        # day 为 date；转为该 UTC 日期 00:00:00.000 的毫秒时间戳
        items.append({"date": _to_day_ms(day), "total_num": int(total_num or 0)})

    business_logger.info(f"按日查询记忆增量完成: days={len(items)}")
    return {"items": items}


# ======== RAG 相关服务 ========
def get_rag_total_doc(
    db: Session,
    current_user: User
) -> int:
    """
    根据当前用户所在的workspace_id查询konwledges表所有doc_num的总和
    """
    workspace_id = current_user.current_workspace_id
    business_logger.info(f"获取RAG总文档数: workspace_id={workspace_id}, 操作者: {current_user.username}")

    try:
        total_doc = knowledge_repository.get_total_doc_num_by_workspace(db, workspace_id)
        business_logger.info(f"成功获取RAG总文档数: {total_doc}")
        return total_doc
    except Exception as e:
        business_logger.error(f"获取RAG总文档数失败: workspace_id={workspace_id} - {str(e)}")
        raise


def get_rag_total_chunk(
    db: Session,
    current_user: User
) -> int:
    """
    RAG 记忆片段数：独立索引中当前空间全部宿主的消息条数。

    字段名沿用 chunk（对外契约不变），口径已由知识库分段改为消息条数。
    """
    workspace_id = current_user.current_workspace_id
    business_logger.info(f"获取RAG记忆片段数: workspace_id={workspace_id}, 操作者: {current_user.username}")

    try:
        total_chunk = rag_stats.count_messages_sync(
            _workspace_end_user_ids(db, workspace_id)
        )
        business_logger.info(f"成功获取RAG记忆片段数: {total_chunk}")
        return total_chunk
    except Exception as e:
        business_logger.error(f"获取RAG记忆片段数失败: workspace_id={workspace_id} - {str(e)}")
        raise


def get_rag_total_kb(
    db: Session,
    current_user: User
) -> int:
    """
    根据当前用户所在的workspace_id查询konwledges表中排除用户知识库（permission_id!='Memory'）的数量
    """
    workspace_id = current_user.current_workspace_id
    business_logger.info(f"获取RAG总知识库数(排除用户知识库): workspace_id={workspace_id}, 操作者: {current_user.username}")

    try:
        total_kb = knowledge_repository.get_non_user_kb_count_by_workspace(db, workspace_id)
        business_logger.info(f"成功获取RAG总知识库数: {total_kb}")
        return total_kb
    except Exception as e:
        business_logger.error(f"获取RAG总知识库数失败: workspace_id={workspace_id} - {str(e)}")
        raise


def get_rag_user_kb_total_chunk(
    db: Session,
    current_user: User
) -> int:
    """
    RAG 用户记忆总量：独立索引中当前空间全部宿主的消息条数。
    """
    workspace_id = current_user.current_workspace_id
    business_logger.info(f"获取RAG用户记忆总量: workspace_id={workspace_id}, 操作者: {current_user.username}")

    try:
        end_user_ids = _workspace_end_user_ids(db, workspace_id)
        total_chunk = rag_stats.count_messages_sync(end_user_ids)

        business_logger.info(f"成功获取RAG用户记忆总量: {total_chunk}")
        return total_chunk
    except Exception as e:
        business_logger.error(f"获取RAG用户记忆总量失败: workspace_id={workspace_id} - {str(e)}")
        raise


async def get_rag_user_kb_total_chunk_async(
    db: AsyncSession,
    current_user
) -> int:
    """get_rag_user_kb_total_chunk 的异步版本，统计口径一致。

    RAG 用户记忆总量 = 独立索引中当前空间全部宿主的消息条数。
    """
    workspace_id = current_user.current_workspace_id
    business_logger.info(f"获取RAG用户记忆总量(异步): workspace_id={workspace_id}, 操作者: {current_user.username}")

    try:
        end_user_ids = await _workspace_end_user_ids_async(db, workspace_id)
        total_chunk = await rag_stats.count_messages(end_user_ids)

        business_logger.info(f"成功获取RAG用户记忆总量(异步): {total_chunk}")
        return total_chunk
    except Exception as e:
        business_logger.error(f"获取RAG用户记忆总量失败(异步): workspace_id={workspace_id} - {str(e)}")
        raise


def get_dashboard_yesterday_changes(
    db: Session,
    workspace_id: uuid.UUID,
    storage_type: str,
    today_data: dict
) -> dict:
    """
    计算各指标相比昨天的变化百分比。

    - total_app_change / total_knowledge_change：只看活跃记录，
      百分比 = (截止今日活跃总量 - 截止昨日活跃总量) / 截止昨日活跃总量
    - total_memory_change / total_api_call_change：
      百分比 = (今日总量 - 昨日总量) / 昨日总量

    昨日总量为 0 时返回 None。返回值为浮点数，例如 0.5 表示增长 50%。

    Args:
        db: 数据库会话
        workspace_id: 工作空间ID
        storage_type: 存储类型 'neo4j' | 'rag'
        today_data: 当前数据，包含 total_memory, total_app, total_knowledge, total_api_call

    Returns:
        {
            "total_memory_change": float | None,
            "total_app_change": float | None,
            "total_knowledge_change": float | None,
            "total_api_call_change": float | None
        }
    """
    from sqlalchemy import func
    from app.models.api_key_model import ApiKey, ApiKeyLog
    from app.models.knowledge_model import Knowledge
    from app.models.app_model import App
    from app.models.appshare_model import AppShare

    business_logger.info(f"计算昨日对比百分比: workspace_id={workspace_id}, storage_type={storage_type}")

    now_local = utcnow_naive()
    today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)

    changes = {
        "total_memory_change": None,
        "total_app_change": None,
        "total_knowledge_change": None,
        "total_api_call_change": None,
    }

    def _calc_percentage(today_val, yesterday_val):
        """计算百分比，昨日为0时返回None"""
        if yesterday_val is None or yesterday_val == 0:
            return None
        return round((today_val - yesterday_val) / yesterday_val, 4)

    # --- total_api_call_change: (截止今日累计总数 - 截止昨日累计总数) / 截止昨日累计总数 ---
    try:
        api_key_ids = [
            row[0] for row in db.query(ApiKey.id).filter(
                ApiKey.workspace_id == workspace_id
            ).all()
        ]
        if api_key_ids:
            # 截止今日的累计调用总数
            total_api_until_now = db.query(func.count(ApiKeyLog.id)).filter(
                ApiKeyLog.api_key_id.in_(api_key_ids),
                ApiKeyLog.created_at < now_local
            ).scalar() or 0
            # 截止昨日的累计调用总数（today_start 即昨日结束）
            total_api_until_yesterday = db.query(func.count(ApiKeyLog.id)).filter(
                ApiKeyLog.api_key_id.in_(api_key_ids),
                ApiKeyLog.created_at < today_start
            ).scalar() or 0
            changes["total_api_call_change"] = _calc_percentage(total_api_until_now, total_api_until_yesterday)
        else:
            changes["total_api_call_change"] = None
    except Exception as e:
        business_logger.warning(f"计算API调用昨日对比失败: {str(e)}")

    # --- total_knowledge_change: 只看活跃(status=1)且为顶层知识库(parent_id=workspace_id)，百分比 = (今日活跃总量 - 昨日活跃总量) / 昨日活跃总量 ---
    try:
        # 截止今日的活跃知识库总量（当前 status=1，parent_id=workspace_id）
        today_knowledge = db.query(func.count(Knowledge.id)).filter(
            Knowledge.workspace_id == workspace_id,
            Knowledge.status == 1,
            Knowledge.parent_id == Knowledge.workspace_id
        ).scalar() or 0
        # 截止昨日的活跃知识库总量（昨日之前创建的、当前仍 status=1，parent_id=workspace_id）
        yesterday_knowledge = db.query(func.count(Knowledge.id)).filter(
            Knowledge.workspace_id == workspace_id,
            Knowledge.status == 1,
            Knowledge.parent_id == Knowledge.workspace_id,
            Knowledge.created_at < today_start
        ).scalar() or 0

        changes["total_knowledge_change"] = _calc_percentage(today_knowledge, yesterday_knowledge)
    except Exception as e:
        business_logger.warning(f"计算知识库昨日对比失败: {str(e)}")

    # --- total_app_change: 只看活跃(is_active=True)，百分比 = (今日活跃总量 - 昨日活跃总量) / 昨日活跃总量 ---
    try:
        # === 自有app ===
        today_own_apps = db.query(func.count(App.id)).filter(
            App.workspace_id == workspace_id,
            App.is_active == True
        ).scalar() or 0
        yesterday_own_apps = db.query(func.count(App.id)).filter(
            App.workspace_id == workspace_id,
            App.is_active == True,
            App.created_at < today_start
        ).scalar() or 0

        # === 被分享app ===
        today_shared_apps = db.query(func.count(AppShare.id)).filter(
            AppShare.target_workspace_id == workspace_id,
            AppShare.is_active == True
        ).scalar() or 0
        yesterday_shared_apps = db.query(func.count(AppShare.id)).filter(
            AppShare.target_workspace_id == workspace_id,
            AppShare.is_active == True,
            AppShare.created_at < today_start
        ).scalar() or 0

        today_total_app = today_own_apps + today_shared_apps
        yesterday_total_app = yesterday_own_apps + yesterday_shared_apps

        changes["total_app_change"] = _calc_percentage(today_total_app, yesterday_total_app)
    except Exception as e:
        business_logger.warning(f"计算应用数量昨日对比失败: {str(e)}")

    # --- total_memory_change: (今日总量 - 昨日总量) / 昨日总量 ---
    try:
        today_memory = today_data.get("total_memory")
        if today_memory is None:
            changes["total_memory_change"] = None
        elif storage_type == "neo4j":
            last_record = db.query(MemoryIncrement).filter(
                MemoryIncrement.workspace_id == workspace_id,
                MemoryIncrement.created_at < today_start
            ).order_by(desc(MemoryIncrement.created_at)).first()
            if last_record is None or last_record.total_num == 0:
                changes["total_memory_change"] = None
            else:
                changes["total_memory_change"] = _calc_percentage(today_memory, last_record.total_num)
        elif storage_type == "rag":
            from app.repositories.end_user_repository import EndUserRepository

            end_user_ids = EndUserRepository(db).get_ids_by_app_workspace(workspace_id)
            if not end_user_ids:
                changes["total_memory_change"] = None
            else:
                yesterday_total = rag_stats.count_messages_sync(
                    end_user_ids,
                    end=today_start - timedelta(microseconds=1),
                )
                if yesterday_total == 0:
                    changes["total_memory_change"] = None
                else:
                    changes["total_memory_change"] = _calc_percentage(today_memory, yesterday_total)
    except Exception as e:
        business_logger.warning(f"计算记忆总量昨日对比失败: {str(e)}")

    business_logger.info(f"昨日对比百分比计算完成: {changes}")
    return changes


@redis_cache(prefix="dashboard_change", id_arg="workspace_id")
async def get_dashboard_yesterday_changes_async(
    db,
    workspace_id: uuid.UUID,
    storage_type: str,
    today_data: dict
) -> dict:
    """get_dashboard_yesterday_changes 的异步版本，统计口径与同步版完全一致。

    只把 db.query(...) 换成 await db.execute(select(...))，查询条数、过滤条件、
    日期边界逐一对应，不做任何合并，以保证数值行为零漂移。

    Args:
        db: AsyncSession
        workspace_id: 工作空间ID
        storage_type: 存储类型 'neo4j' | 'rag'
        today_data: 当前数据，含 total_memory / total_app / total_knowledge / total_api_call

    Returns:
        dict: total_memory_change / total_app_change / total_knowledge_change / total_api_call_change
    """
    from sqlalchemy import func, select
    from app.models.api_key_model import ApiKey, ApiKeyLog
    from app.models.knowledge_model import Knowledge
    from app.models.app_model import App
    from app.models.appshare_model import AppShare

    business_logger.info(f"计算昨日对比百分比(异步): workspace_id={workspace_id}, storage_type={storage_type}")

    now_local = utcnow_naive()
    today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)

    changes = {
        "total_memory_change": None,
        "total_app_change": None,
        "total_knowledge_change": None,
        "total_api_call_change": None,
    }

    def _calc_percentage(today_val, yesterday_val):
        """计算百分比，昨日为0时返回None"""
        if yesterday_val is None or yesterday_val == 0:
            return None
        return round((today_val - yesterday_val) / yesterday_val, 4)

    # --- total_api_call_change: (截止今日累计总数 - 截止昨日累计总数) / 截止昨日累计总数 ---
    try:
        api_key_ids = [
            row[0] for row in (await db.execute(
                select(ApiKey.id).where(ApiKey.workspace_id == workspace_id)
            )).all()
        ]
        if api_key_ids:
            # 截止今日的累计调用总数
            total_api_until_now = (await db.execute(
                select(func.count(ApiKeyLog.id)).where(
                    ApiKeyLog.api_key_id.in_(api_key_ids),
                    ApiKeyLog.created_at < now_local,
                )
            )).scalar() or 0
            # 截止昨日的累计调用总数（today_start 即昨日结束）
            total_api_until_yesterday = (await db.execute(
                select(func.count(ApiKeyLog.id)).where(
                    ApiKeyLog.api_key_id.in_(api_key_ids),
                    ApiKeyLog.created_at < today_start,
                )
            )).scalar() or 0
            changes["total_api_call_change"] = _calc_percentage(total_api_until_now, total_api_until_yesterday)
        else:
            changes["total_api_call_change"] = None
    except Exception as e:
        business_logger.warning(f"计算API调用昨日对比失败: {str(e)}")

    # --- total_knowledge_change: 只看活跃(status=1)且为顶层知识库(parent_id=workspace_id) ---
    try:
        # 截止今日的活跃知识库总量
        today_knowledge = (await db.execute(
            select(func.count(Knowledge.id)).where(
                Knowledge.workspace_id == workspace_id,
                Knowledge.status == 1,
                Knowledge.parent_id == Knowledge.workspace_id,
            )
        )).scalar() or 0
        # 截止昨日的活跃知识库总量
        yesterday_knowledge = (await db.execute(
            select(func.count(Knowledge.id)).where(
                Knowledge.workspace_id == workspace_id,
                Knowledge.status == 1,
                Knowledge.parent_id == Knowledge.workspace_id,
                Knowledge.created_at < today_start,
            )
        )).scalar() or 0

        changes["total_knowledge_change"] = _calc_percentage(today_knowledge, yesterday_knowledge)
    except Exception as e:
        business_logger.warning(f"计算知识库昨日对比失败: {str(e)}")

    # --- total_app_change: 只看活跃(is_active=True) ---
    try:
        # === 自有app ===
        today_own_apps = (await db.execute(
            select(func.count(App.id)).where(
                App.workspace_id == workspace_id,
                App.is_active.is_(True),
            )
        )).scalar() or 0
        yesterday_own_apps = (await db.execute(
            select(func.count(App.id)).where(
                App.workspace_id == workspace_id,
                App.is_active.is_(True),
                App.created_at < today_start,
            )
        )).scalar() or 0

        # === 被分享app ===
        today_shared_apps = (await db.execute(
            select(func.count(AppShare.id)).where(
                AppShare.target_workspace_id == workspace_id,
                AppShare.is_active.is_(True),
            )
        )).scalar() or 0
        yesterday_shared_apps = (await db.execute(
            select(func.count(AppShare.id)).where(
                AppShare.target_workspace_id == workspace_id,
                AppShare.is_active.is_(True),
                AppShare.created_at < today_start,
            )
        )).scalar() or 0

        today_total_app = today_own_apps + today_shared_apps
        yesterday_total_app = yesterday_own_apps + yesterday_shared_apps

        changes["total_app_change"] = _calc_percentage(today_total_app, yesterday_total_app)
    except Exception as e:
        business_logger.warning(f"计算应用数量昨日对比失败: {str(e)}")

    # --- total_memory_change: (今日总量 - 昨日总量) / 昨日总量 ---
    try:
        today_memory = today_data.get("total_memory")
        if today_memory is None:
            changes["total_memory_change"] = None
        elif storage_type == "neo4j":
            last_record = (await db.execute(
                select(MemoryIncrement).where(
                    MemoryIncrement.workspace_id == workspace_id,
                    MemoryIncrement.created_at < today_start,
                ).order_by(desc(MemoryIncrement.created_at)).limit(1)
            )).scalars().first()
            if last_record is None or last_record.total_num == 0:
                changes["total_memory_change"] = None
            else:
                changes["total_memory_change"] = _calc_percentage(today_memory, last_record.total_num)
        elif storage_type == "rag":
            from app.repositories.end_user_repository import EndUserRepository

            end_user_ids = await EndUserRepository(db).get_ids_by_app_workspace_async(workspace_id)
            if not end_user_ids:
                changes["total_memory_change"] = None
            else:
                yesterday_total = await rag_stats.count_messages(
                    end_user_ids,
                    end=today_start - timedelta(microseconds=1),
                )
                if yesterday_total == 0:
                    changes["total_memory_change"] = None
                else:
                    changes["total_memory_change"] = _calc_percentage(today_memory, yesterday_total)
    except Exception as e:
        business_logger.warning(f"计算记忆总量昨日对比失败: {str(e)}")

    business_logger.info(f"昨日对比百分比计算完成(异步): {changes}")
    return changes


def get_current_user_total_chunk(
    end_user_id: str,
    db: Session,
    current_user: User
) -> int:
    """
    RAG 宿主记忆总量：独立索引中该宿主的消息条数。
    """
    business_logger.info(f"获取宿主记忆总量: end_user_id={end_user_id}, 操作者: {current_user.username}")

    try:
        total_chunk = rag_stats.count_messages_sync([end_user_id])
        business_logger.info(f"成功获取宿主记忆总量: {total_chunk}")
        return int(total_chunk)

    except Exception as e:
        business_logger.error(f"获取宿主记忆总量失败: end_user_id={end_user_id} - {str(e)}")
        raise


def _resolve_rag_scope_ids(
    db: Session,
    current_user: User,
    end_user_id: Optional[str],
) -> List[str]:
    """RAG 记忆查询范围：传宿主=单宿主，不传=当前空间全部宿主。"""
    if end_user_id:
        return [end_user_id]
    return _workspace_end_user_ids(db, current_user.current_workspace_id)


async def _resolve_rag_scope_ids_async(
    db: AsyncSession,
    current_user: User,
    end_user_id: Optional[str],
) -> List[str]:
    if end_user_id:
        return [end_user_id]
    return await _workspace_end_user_ids_async(db, current_user.current_workspace_id)


def get_rag_content(
    end_user_id: Optional[str],
    page: int,
    pagesize: int,
    db: Session,
    current_user: User,
    *,
    start_time: Optional[int] = None,
    end_time: Optional[int] = None,
    source: Optional[List[str]] = None,
    keyword: Optional[str] = None,
    order: str = "desc",
) -> dict:
    """读取 RAG 记忆消息（分页，默认最新在前）。

    数据源为独立索引 `rag_memory_current`：
    - end_user_id 传值时只查该宿主，不传时查当前空间全部宿主
    - start_time/end_time 为毫秒 UTC 闭区间
    - source 支持多值过滤，keyword 走 content 全文匹配
    """
    business_logger.info(
        f"获取RAG内容: end_user_id={end_user_id}, page={page}, pagesize={pagesize}, "
        f"source={source}, keyword={keyword}, 操作者: {current_user.username}"
    )

    try:
        from app.core.utils.datetime_utils import parse_timestamp_to_utc_naive

        end_user_ids = _resolve_rag_scope_ids(db, current_user, end_user_id)
        result = rag_stats.list_messages_sync(
            end_user_ids,
            page=page,
            pagesize=pagesize,
            sources=source,
            keyword=keyword,
            start=parse_timestamp_to_utc_naive(start_time),
            end=parse_timestamp_to_utc_naive(end_time),
            order=order,
        )
        business_logger.info(
            f"成功获取RAG内容: page={page}, 返回={len(result.get('items', []))} 条消息"
        )
        return result

    except Exception as e:
        business_logger.error(f"获取RAG内容失败: end_user_id={end_user_id} - {str(e)}")
        raise


async def get_rag_content_async(
    end_user_id: Optional[str],
    page: int,
    pagesize: int,
    db: AsyncSession,
    current_user: User,
    *,
    start_time: Optional[int] = None,
    end_time: Optional[int] = None,
    source: Optional[List[str]] = None,
    keyword: Optional[str] = None,
    order: str = "desc",
) -> dict:
    """get_rag_content 的异步版本（供异步链路直接 await）。"""
    from app.core.utils.datetime_utils import parse_timestamp_to_utc_naive

    end_user_ids = await _resolve_rag_scope_ids_async(db, current_user, end_user_id)
    return await rag_stats.list_messages(
        end_user_ids,
        page=page,
        pagesize=pagesize,
        sources=source,
        keyword=keyword,
        start=parse_timestamp_to_utc_naive(start_time),
        end=parse_timestamp_to_utc_naive(end_time),
        order=order,
    )


async def get_chunk_summary_and_tags(
    end_user_id: str,
    limit: int,
    max_tags: int,
    db: Session,
    current_user: User
) -> dict:
    """
    纯读库：从end_user表返回RAG摘要、标签和人物形象缓存。
    无数据时返回空结构，不触发LLM生成。
    """
    import json
    from app.repositories.end_user_repository import EndUserRepository

    business_logger.info(f"读取chunk摘要/标签/人物形象缓存: end_user_id={end_user_id}")

    repo = EndUserRepository(db)
    end_user = repo.get_by_id(uuid.UUID(end_user_id))

    if not end_user:
        return {"summary": "", "tags": [], "personas": [], "generated": False}

    return {
        "summary": end_user.user_summary or "",
        "tags": json.loads(end_user.rag_tags) if end_user.rag_tags else [],
        "personas": json.loads(end_user.rag_personas) if end_user.rag_personas else [],
        "generated": bool(end_user.user_summary),
    }


async def get_chunk_insight(
    end_user_id: str,
    limit: int,
    db: Session,
    current_user: User
) -> dict:
    """
    纯读库：从end_user表返回RAG洞察缓存。
    无数据时返回空结构，不触发LLM生成。
    """
    from app.repositories.end_user_repository import EndUserRepository

    business_logger.info(f"读取chunk洞察缓存: end_user_id={end_user_id}")

    repo = EndUserRepository(db)
    end_user = repo.get_by_id(uuid.UUID(end_user_id))

    if not end_user:
        return {"insight": "", "behavior_pattern": "", "key_findings": "", "growth_trajectory": "", "generated": False}

    return {
        "insight": end_user.memory_insight or "",
        "behavior_pattern": end_user.behavior_pattern or "",
        "key_findings": end_user.key_findings or "",
        "growth_trajectory": end_user.growth_trajectory or "",
        "generated": bool(end_user.memory_insight),
    }


async def generate_rag_profile(
    end_user_id: str,
    limit: int,
    max_tags: int,
    db: Session,
    current_user: User,
) -> dict:
    """
    生产接口：为RAG存储模式的end_user全量重新生成并持久化完整画像数据。
    每次调用都会重新生成，覆盖已有数据。

    生成内容：
      - user_summary / rag_tags / rag_personas
      - memory_insight / behavior_pattern / key_findings / growth_trajectory
    """
    import json
    import asyncio
    from app.repositories.end_user_repository import EndUserRepository
    from app.core.rag_utils import (
        generate_chunk_summary,
        extract_chunk_tags,
        extract_chunk_persona,
        generate_chunk_insight_sections,
    )

    business_logger.info(f"开始生产RAG画像: end_user_id={end_user_id}, 操作者: {current_user.username}")

    repo = EndUserRepository(db)
    end_user = repo.get_by_id(uuid.UUID(end_user_id))

    if not end_user:
        raise ValueError(f"end_user {end_user_id} 不存在")

    rag_content = get_rag_content(end_user_id, page=1, pagesize=limit, db=db, current_user=current_user)
    chunks = rag_content.get("items", [])

    if not chunks:
        business_logger.warning(f"未找到chunk内容，无法生产RAG画像: end_user_id={end_user_id}")
        raise ValueError("暂无chunk内容，无法生成画像")

    summary, tags_with_freq, personas, insight_sections = await asyncio.gather(
        generate_chunk_summary(chunks, max_chunks=limit, end_user_id=end_user_id),
        extract_chunk_tags(chunks, max_tags=max_tags, max_chunks=limit, end_user_id=end_user_id),
        extract_chunk_persona(chunks, max_personas=5, max_chunks=limit, end_user_id=end_user_id),
        generate_chunk_insight_sections(chunks, max_chunks=limit, end_user_id=end_user_id),
    )

    tags = [{"tag": tag, "frequency": freq} for tag, freq in tags_with_freq]

    repo.update_rag_summary_tags(
        end_user_id=end_user.id,
        user_summary=summary,
        rag_tags=json.dumps(tags, ensure_ascii=False),
        rag_personas=json.dumps(personas, ensure_ascii=False),
    )

    repo.update_memory_insight(
        end_user_id=end_user.id,
        memory_insight=insight_sections.get("memory_insight", ""),
        behavior_pattern=insight_sections.get("behavior_pattern", ""),
        key_findings=insight_sections.get("key_findings", ""),
        growth_trajectory=insight_sections.get("growth_trajectory", ""),
    )

    business_logger.info(f"RAG画像生产完成: end_user_id={end_user_id}, tags={len(tags)}, personas={len(personas)}")

    return {
        "end_user_id": end_user_id,
        "summary_length": len(summary),
        "tags_count": len(tags),
        "personas_count": len(personas),
        "insight_generated": bool(insight_sections.get("memory_insight")),
    }


def get_dashboard_common_stats(db: Session, workspace_id) -> dict:
    """
    获取 dashboard 中 neo4j/rag 分支共享的统计数据：
    total_app、total_knowledge、total_api_call

    Returns:
        dict: {"total_app": int, "total_knowledge": int, "total_api_call": int}
    """
    result = {"total_app": 0, "total_knowledge": 0, "total_api_call": 0}

    # total_app: 统计当前空间下的所有app数量（包含自有 + 被分享给本工作空间的app）
    try:
        from app.services import app_service as _app_svc
        _, total_app = _app_svc.AppService(db).list_apps(
            workspace_id=workspace_id, include_shared=True, pagesize=1
        )
        result["total_app"] = total_app
    except Exception as e:
        business_logger.warning(f"获取应用数量失败: {e}")

    # total_knowledge: 统计顶层知识库（parent_id = workspace_id）
    try:
        from sqlalchemy import func as _func
        from app.models.knowledge_model import Knowledge as _Knowledge
        total_knowledge = db.query(_func.count(_Knowledge.id)).filter(
            _Knowledge.workspace_id == workspace_id,
            _Knowledge.status == 1,
            _Knowledge.parent_id == _Knowledge.workspace_id
        ).scalar() or 0
        result["total_knowledge"] = total_knowledge
    except Exception as e:
        business_logger.warning(f"获取知识库数量失败: {e}")

    # total_api_call: 截止当前的历史累计调用总数
    try:
        from sqlalchemy import func as _api_func
        from app.models.api_key_model import ApiKey as _ApiKey, ApiKeyLog as _ApiKeyLog

        _api_key_ids = [
            row[0] for row in db.query(_ApiKey.id).filter(
                _ApiKey.workspace_id == workspace_id
            ).all()
        ]
        if _api_key_ids:
            total_api_calls = db.query(_api_func.count(_ApiKeyLog.id)).filter(
                _ApiKeyLog.api_key_id.in_(_api_key_ids)
            ).scalar() or 0
        else:
            total_api_calls = 0
        result["total_api_call"] = total_api_calls
    except Exception as e:
        business_logger.warning(f"获取API调用统计失败: {e}")

    return result


@redis_cache(prefix="common_status", skip_args=["db"], id_arg="workspace_id")
async def get_dashboard_common_stats_async(db, workspace_id) -> dict:
    """get_dashboard_common_stats 的异步版本，统计口径一致：
    total_app、total_knowledge、total_api_call。

    其中 total_app 原实现走 AppService.list_apps(include_shared=True, pagesize=1) 只为
    取一个 total；此处按该方法的计数语义等价改写为单条 count 查询——
    count(App WHERE is_active AND (workspace_id = ws OR id IN 活跃分享的 source_app_id))，
    避免为拿一个数字而走整条分页链路。

    Returns:
        dict: {"total_app": int, "total_knowledge": int, "total_api_call": int}
    """
    from sqlalchemy import func, or_, select
    from app.models.knowledge_model import Knowledge
    from app.models.app_model import App
    from app.models.appshare_model import AppShare
    from app.models.api_key_model import ApiKey, ApiKeyLog

    result = {"total_app": 0, "total_knowledge": 0, "total_api_call": 0}

    # total_app: 统计当前空间下的所有app数量（自有 + 被分享给本工作空间的app）
    try:
        shared_app_ids_stmt = (
            select(AppShare.source_app_id)
            .where(AppShare.target_workspace_id == workspace_id, AppShare.is_active.is_(True))
        )
        app_stmt = select(App.id).where(
            App.is_active.is_(True),
            or_(App.workspace_id == workspace_id, App.id.in_(shared_app_ids_stmt)),
        )
        result["total_app"] = int(
            (await db.execute(select(func.count()).select_from(app_stmt.subquery()))).scalar() or 0
        )
    except Exception as e:
        business_logger.warning(f"获取应用数量失败: {e}")

    # total_knowledge: 统计顶层知识库（parent_id = workspace_id）
    try:
        total_knowledge = (await db.execute(
            select(func.count(Knowledge.id)).where(
                Knowledge.workspace_id == workspace_id,
                Knowledge.status == 1,
                Knowledge.parent_id == Knowledge.workspace_id,
            )
        )).scalar() or 0
        result["total_knowledge"] = total_knowledge
    except Exception as e:
        business_logger.warning(f"获取知识库数量失败: {e}")

    # total_api_call: 截止当前的历史累计调用总数
    try:
        api_key_ids = [
            row[0] for row in (await db.execute(
                select(ApiKey.id).where(ApiKey.workspace_id == workspace_id)
            )).all()
        ]
        if api_key_ids:
            total_api_calls = (await db.execute(
                select(func.count(ApiKeyLog.id)).where(
                    ApiKeyLog.api_key_id.in_(api_key_ids)
                )
            )).scalar() or 0
        else:
            total_api_calls = 0
        result["total_api_call"] = total_api_calls
    except Exception as e:
        business_logger.warning(f"获取API调用统计失败: {e}")

    return result


@redis_cache(ttl=60, prefix="active_counts")
async def batch_get_active_counts(
    end_user_ids: tuple[str, ...],
) -> dict[str, int]:
    """批量查询 Neo4j 活跃节点数（Statement + Chunk + ExtractedEntity，delete_at IS NULL）。

    结果通过 ``@redis_cache`` 缓存 60 秒。
    ``end_user_ids`` 使用 tuple 以确保参数可哈希（装饰器需要）。
    """
    from app.repositories.neo4j.graph_search import forget_count_active_nodes_batch
    from app.repositories.neo4j.neo4j_connector import Neo4jConnector

    async with Neo4jConnector() as conn:
        return await forget_count_active_nodes_batch(conn, list(end_user_ids))


def batch_get_memory_limits(
    db: Session,
    end_user_ids: list[str],
) -> dict[str, int]:
    """批量获取每个 end_user 的 memory_limit（来自租户套餐配额，默认 300）。"""
    from uuid import UUID as _UUID

    from app.core.quota_manager import get_end_user_memory_limit
    from app.repositories.end_user_repository import get_tenant_id_by_end_user_id

    result: dict[str, int] = {}
    for uid in end_user_ids:
        try:
            tenant_id = get_tenant_id_by_end_user_id(db, _UUID(uid))
            limit = get_end_user_memory_limit(db, tenant_id) or 300
        except Exception:
            limit = 300
        result[uid] = limit
    return result
