import uuid
from typing import List, Optional, Dict, Any

from fastapi import HTTPException
from sqlalchemy import desc, nullslast
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.logging_config import get_business_logger
from app.core.utils.datetime_utils import utcnow_naive
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


async def get_workspace_memory_increment_async(
    db,
    workspace_id: uuid.UUID,
    limit: int,
    current_user
) -> List[MemoryIncrementSchema]:
    """获取工作空间的记忆增量（异步版本）"""
    business_logger.info(f"获取记忆增量(异步): workspace_id={workspace_id}, limit={limit}, 操作者: {current_user.username}")
    from sqlalchemy import select, func

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
            total_memory_record = await memory_increment_repository.write_memory_increment(db, workspace_id, 0)
            result["total_memory"] = total_memory_record.total_num
            business_logger.info(f"成功获取记忆总量: {total_memory_record.total_num}")
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


@redis_cache(prefix="memory_count_total", id_arg="workspace_id", skip_args=["current_user"])
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

    Returns:
        int: 记忆总量；未找到指定宿主或无活跃宿主时为 0
    """
    business_logger.info(
        f"获取工作空间记忆总量(仅总量): workspace_id={workspace_id}, 操作者: {current_user.username}"
    )

    try:
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

    except HTTPException:
        raise
    except Exception as e:
        business_logger.error(f"获取工作空间记忆总量(仅总量)失败: workspace_id={workspace_id} - {str(e)}")
        raise


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


@redis_cache(prefix="memory_count", id_arg="workspace_id", skip_args=["current_user"])
async def get_workspace_total_memory_count_async(
    db,
    workspace_id: uuid.UUID,
    current_user,
    end_user_id: str = None
) -> dict:
    """获取工作空间的记忆总量（异步版本，使用 AsyncSession）。

    数据来源：直接读取 end_users.memory_count 字段聚合，不再实时扫描 Neo4j。
    memory_count 由写入/遗忘等流程维护，可能相对 Neo4j 实时计数有极小滞后，
    但换取的是数量级的耗时下降（全库扫描 -> 单条索引聚合）。
    """
    business_logger.info(f"获取工作空间记忆总量(异步): workspace_id={workspace_id}, 操作者: {current_user.username}")

    try:
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

    except HTTPException:
        raise
    except Exception as e:
        business_logger.error(f"获取工作空间记忆总量失败(异步): workspace_id={workspace_id} - {str(e)}")
        raise


async def get_end_user_memory_counts_async(
    db,
    workspace_id: uuid.UUID,
    end_user_ids: List[uuid.UUID],
) -> Dict[str, Any]:
    """批量查询终端用户记忆量（读 end_users.memory_count 字段）。

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

    items = [
        {
            "end_user_id": str(row.id),
            "other_name": row.other_name or None,
            "memory_count": int(row.memory_count or 0),
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
    """按时间段逐日返回记忆增量总量（同日取当日最新一条）。

    过滤 memory_increments.created_at（闭区间）；start_ms/end_ms 为毫秒 UTC 时间戳。
    返回的 date 为代表日期的 UTC 当日 00:00:00.000 毫秒时间戳。

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

    repo = MemoryIncrementRepository(db)
    rows = await repo.get_daily_latest_by_workspace_async(workspace_id, start_dt, end_dt)

    items = []
    for day, total_num in rows:
        # day 为 date；转为该 UTC 日期 00:00:00.000 的毫秒时间戳
        day_ms = int(
            datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() * 1000
        )
        items.append({"date": day_ms, "total_num": int(total_num or 0)})

    business_logger.info(f"按日查询记忆增量完成: days={len(items)}")
    return {"items": items}


# ======== RAG 相关服务 ========








async def get_rag_user_kb_total_chunk_async(
    db: AsyncSession,
    current_user
) -> int:
    """get_rag_user_kb_total_chunk 的异步版本，统计口径一致。

    与 /end_users 接口同源：查询 file_name 匹配 end_user_id.txt 的文档 chunk_num 之和。
    """
    workspace_id = current_user.current_workspace_id
    business_logger.info(f"获取用户知识库总chunk数(documents表,异步): workspace_id={workspace_id}, 操作者: {current_user.username}")

    try:
        from app.models.document_model import Document
        from app.repositories.end_user_repository import EndUserRepository
        from sqlalchemy import func, select

        # 通过 App 关联取该 workspace 下所有活跃 end_user_id
        end_user_ids = await EndUserRepository(db).get_ids_by_app_workspace_async(workspace_id)
        if not end_user_ids:
            return 0

        file_names = [f"{uid}.txt" for uid in end_user_ids]
        result = (await db.execute(
            select(func.sum(Document.chunk_num)).where(
                Document.file_name.in_(file_names)
            )
        )).scalar()

        total_chunk = int(result or 0)
        business_logger.info(f"成功获取用户知识库总chunk数(异步): {total_chunk}")
        return total_chunk
    except Exception as e:
        await db.rollback()
        business_logger.error(f"获取用户知识库总chunk数失败(异步): workspace_id={workspace_id} - {str(e)}")
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
            from app.models.document_model import Document
            from app.repositories.end_user_repository import EndUserRepository

            end_user_ids = await EndUserRepository(db).get_ids_by_app_workspace_async(workspace_id)
            if not end_user_ids:
                changes["total_memory_change"] = None
            else:
                file_names = [f"{uid}.txt" for uid in end_user_ids]
                yesterday_chunk = int((await db.execute(
                    select(func.sum(Document.chunk_num)).where(
                        Document.file_name.in_(file_names),
                        Document.created_at < today_start,
                    )
                )).scalar() or 0)
                if yesterday_chunk == 0:
                    changes["total_memory_change"] = None
                else:
                    changes["total_memory_change"] = _calc_percentage(today_memory, yesterday_chunk)
    except Exception as e:
        business_logger.warning(f"计算记忆总量昨日对比失败: {str(e)}")

    business_logger.info(f"昨日对比百分比计算完成(异步): {changes}")
    return changes










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
