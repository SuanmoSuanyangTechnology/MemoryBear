"""Dashboard service API - based on API Key authentication

Query the end user list and each end user's memory count within the workspace
bound to a workspace-level API Key.

Note: `/end_users` intentionally reuses the manager-side route function
(`memory_dashboard_controller.get_workspace_end_users`) instead of the shared
service layer, and is excluded from the "/api and /v1 as two independent entry
points over the same service functions" refactoring. The newer endpoints
(`/total_memory_count`, `/end_user_memory_counts`, `/memory_increment_daily`,
`/dashboard_data`) call the service layer directly.
"""

import uuid
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Query, Request, status
from fastapi.responses import JSONResponse

from app.controllers import memory_dashboard_controller
from app.core.api_key_auth import get_current_api_key_auth, require_api_key_self_db
from app.core.api_key_utils import get_current_user_snapshot_from_api_key_async
from app.core.error_codes import BizCode
from app.core.logging_config import get_business_logger
from app.core.response_utils import fail, success
from app.db import get_async_db_context, get_db_context
from app.schemas.api_key_schema import ApiKeyAuth
from app.schemas.memory_dashboard_schema import EndUserMemoryCountsRequest
from app.schemas.response_schema import ApiResponse
from app.services import memory_dashboard_service, workspace_service

router = APIRouter(prefix="/dashboard", tags=["V1 - Dashboard API"])
api_logger = get_business_logger()


@router.get("/end_users", response_model=ApiResponse)
@require_api_key_self_db(scopes=["memory"])
async def get_workspace_end_users(
    request: Request,
    background_tasks: BackgroundTasks,
    api_key_auth: ApiKeyAuth = None,
    keyword: Optional[str] = Query(None, description="Search keyword (fuzzy match on other_name and id)"),
    label: Optional[str] = Query(None, description="Label filter (long=has name, short=no name)"),
    page: int = Query(1, ge=1, description="Page number, starting from 1"),
    pagesize: int = Query(10, ge=1, description="Page size"),
):
    """
    Query the end user list and each end user's memory count within the workspace
    bound to the API Key (paginated, fuzzy search supported).

    The workspace is determined by the API Key and is not accepted as input, to
    prevent cross-workspace access. The response shape matches the manager-side
    `GET /dashboard/end_users`: `items[].memory_num.total` is the memory count
    of that end user.

    Args:
        keyword: Search keyword (optional, fuzzy match on other_name and id)
        page: Page number (starting from 1, default 1)
        pagesize: Page size (default 10)

    Returns:
        ApiResponse: end user list with pagination metadata
    """
    # 1. 异步提取用户快照
    async with get_async_db_context() as auth_db:
        current_user = await get_current_user_snapshot_from_api_key_async(auth_db, api_key_auth)

    # 2. Delegate to the manager-side logic with a fresh sync session
    #    (dashboard 内部查询暂未全量异步化，保留同步 session 给业务逻辑)
    with get_db_context() as db:
        return await memory_dashboard_controller.get_workspace_end_users(
            background_tasks=background_tasks,
            workspace_id=api_key_auth.workspace_id,
            keyword=keyword,
            label=label,
            page=page,
            pagesize=pagesize,
            db=db,
            current_user=current_user,
        )


@router.get("/total_memory_count", response_model=ApiResponse)
@require_api_key_self_db(scopes=["memory"])
async def get_workspace_total_memory_count(
    request: Request,
    background_tasks: BackgroundTasks,
    api_key_auth: ApiKeyAuth = None,
):
    """
    Query the total memory count within the workspace bound to the API Key.

    The workspace is determined by the API Key and is not accepted as input, to
    prevent cross-workspace access. The response shape matches the manager-side
    `GET /dashboard/total_memory_count`: data contains total_memory_count /
    host_count / details.

    Returns:
        ApiResponse: total memory count
    """
    # 1. 异步提取用户快照（snapshot.current_workspace_id = API Key 绑定空间）
    async with get_async_db_context() as auth_db:
        current_user = await get_current_user_snapshot_from_api_key_async(auth_db, api_key_auth)

    # 2. 直接调用 service 层：空间由 API Key 绑定空间决定，不接受外部指定 end_user_id
    async with get_async_db_context() as db:
        result = await memory_dashboard_service.get_workspace_total_memory_count_async(
            db=db,
            workspace_id=api_key_auth.workspace_id,
            current_user=current_user,
            end_user_id=None,
        )

    return success(data=result, msg="记忆总量获取成功")


@router.post("/end_user_memory_counts", response_model=ApiResponse)
@require_api_key_self_db(scopes=["memory"])
async def get_end_user_memory_counts(
    request: Request,
    payload: EndUserMemoryCountsRequest,
):
    """
    Batch query end users' memory counts within the workspace bound to the API Key.

    The workspace is determined by the API Key and is NOT accepted as input; any
    `workspace_id` in the request body is ignored to prevent cross-workspace access.
    Reads `end_users.memory_count`; ids not belonging to the workspace or inactive
    trigger a validation error (no partial results).

    Returns:
        ApiResponse: {"total": int, "items": [{end_user_id, other_name, memory_count}]}
    """
    # api_key_auth 不放进签名（否则 ApiKeyAuth 模型会与 payload 一起被当成 body，
    # 触发 FastAPI embed 模式导致 422）；改用装饰器注入的 ContextVar 取值。
    api_key_auth = get_current_api_key_auth()

    async with get_async_db_context() as auth_db:
        current_user = await get_current_user_snapshot_from_api_key_async(auth_db, api_key_auth)

    # 空间恒为 API Key 绑定空间，不接受跨空间指定
    workspace_id = api_key_auth.workspace_id
    if workspace_id is None:
        return fail(BizCode.INVALID_PARAMETER, "API Key 未绑定工作空间", "workspace_id is None")

    end_user_ids = payload.end_user_ids or []
    if not end_user_ids:
        return fail(BizCode.MISSING_PARAMETER, "end_user_ids 不能为空", "end_user_ids is required")
    if len(end_user_ids) > 200:
        return fail(BizCode.INVALID_PARAMETER, "end_user_ids 单次上限 200 个", f"got {len(end_user_ids)}")

    try:
        parsed_ids = [uuid.UUID(str(x)) for x in end_user_ids]
    except (ValueError, AttributeError):
        return fail(BizCode.INVALID_PARAMETER, "存在非法的 end_user_id", "invalid UUID in end_user_ids")

    api_logger.info(
        f"[V1] 批量查询终端用户记忆量: workspace={workspace_id}, count={len(parsed_ids)}"
    )
    async with get_async_db_context() as db:
        result = await memory_dashboard_service.get_end_user_memory_counts_async(
            db=db,
            workspace_id=workspace_id,
            end_user_ids=parsed_ids,
        )

    # 严格校验：请求的 id 必须全部属于该空间且有效，否则整单报错并列出问题 id。
    # （service 只返回命中项；未命中即「不属于当前空间或不存在」）
    found_ids = {item["end_user_id"] for item in result["items"]}
    invalid_ids = [str(x) for x in parsed_ids if str(x) not in found_ids]
    if invalid_ids:
        return fail(
            BizCode.INVALID_PARAMETER,
            "存在不属于当前工作空间或不存在的 end_user_id",
            f"invalid end_user_ids: {invalid_ids}",
        )

    return success(data=result, msg="查询成功")


@router.get("/memory_increment_daily", response_model=ApiResponse)
@require_api_key_self_db(scopes=["memory"])
async def get_memory_increment_daily(
    request: Request,
    background_tasks: BackgroundTasks,
    start_time: int = Query(..., description="Start time in ms (UTC, inclusive)"),
    end_time: int = Query(..., description="End time in ms (UTC, inclusive)"),
    api_key_auth: ApiKeyAuth = None,
):
    """
    Daily memory increments within the workspace bound to the API Key.

    The workspace is determined by the API Key and is not accepted as input. Same-day
    multiple rows resolve to the latest `created_at` row. `items[].date` is the UTC
    midnight (00:00:00.000) millisecond timestamp of the representative day.

    Returns:
        ApiResponse: {"items": [{date, total_num}]}
    """
    async with get_async_db_context() as auth_db:
        current_user = await get_current_user_snapshot_from_api_key_async(auth_db, api_key_auth)

    workspace_id = api_key_auth.workspace_id
    if workspace_id is None:
        return fail(BizCode.INVALID_PARAMETER, "API Key 未绑定工作空间", "workspace_id is None")

    if start_time > end_time:
        return fail(
            BizCode.INVALID_PARAMETER,
            "start_time 不能大于 end_time",
            f"start={start_time}, end={end_time}",
        )

    api_logger.info(
        f"[V1] 查询逐日记忆增量: workspace={workspace_id}, start={start_time}, end={end_time}"
    )
    async with get_async_db_context() as db:
        result = await memory_dashboard_service.get_memory_increment_daily_async(
            db=db,
            workspace_id=workspace_id,
            start_ms=start_time,
            end_ms=end_time,
        )

    return success(data=result, msg="查询成功")


@router.get("/dashboard_data", response_model=ApiResponse)
@require_api_key_self_db(scopes=["memory"])
async def get_dashboard_data(
    request: Request,
    api_key_auth: ApiKeyAuth = None,
):
    """
    Aggregated dashboard data for the workspace bound to the API Key.

    The workspace is determined by the API Key and is NOT accepted as input, to
    prevent cross-workspace access. The response shape matches the manager-side
    `GET /dashboard/dashboard_data`: `storage_type` plus exactly one of
    `neo4j_data` / `rag_data` (the other stays null), each holding total_memory /
    total_app / total_knowledge / total_api_call and their day-over-day changes.

    Returns:
        ApiResponse: {"storage_type", "neo4j_data": {...} | null, "rag_data": {...} | null}
    """
    # 1. 异步提取用户快照（snapshot.current_workspace_id = API Key 绑定空间）
    async with get_async_db_context() as auth_db:
        current_user = await get_current_user_snapshot_from_api_key_async(auth_db, api_key_auth)

    workspace_id = api_key_auth.workspace_id
    if workspace_id is None:
        return fail(BizCode.INVALID_PARAMETER, "API Key 未绑定工作空间", "workspace_id is None")

    api_logger.info(f"[V1] 查询dashboard整合数据: workspace={workspace_id}")

    async with get_async_db_context() as db:
        # 空间来自 API Key，无需再做成员权限校验（与 V1 其它接口一致）
        storage_type = await workspace_service.get_workspace_storage_type_without_auth_async(
            db=db,
            workspace_id=workspace_id,
        )
        if storage_type is None:
            storage_type = "neo4j"

        # 只回填与 storage_type 匹配的那一份数据，另一份保持 null
        result = {
            "storage_type": storage_type,
            "neo4j_data": None,
            "rag_data": None,
        }

        try:
            if storage_type == "neo4j":
                neo4j_data: dict[str, int | None] = {
                    "total_memory": None,
                    "total_app": None,
                    "total_knowledge": None,
                    "total_api_call": None,
                }

                # 1) 记忆总量：neo4j 独有口径，走仅总量版本（不拉取宿主明细）
                try:
                    neo4j_data["total_memory"] = (
                        await memory_dashboard_service.get_workspace_total_memory_count_only_async(
                            db=db,
                            workspace_id=workspace_id,
                            current_user=current_user,
                            end_user_id=None,
                        )
                    )
                    api_logger.info(f"[V1] 成功获取记忆总量: {neo4j_data['total_memory']}")
                except Exception as e:
                    api_logger.warning(f"[V1] 获取记忆总量失败: {str(e)}")

                # 2) 共享统计（total_app、total_knowledge、total_api_call）
                common_stats = await memory_dashboard_service.get_dashboard_common_stats_async(
                    db, workspace_id
                )
                neo4j_data.update(common_stats)

                # 3) 昨日对比
                try:
                    changes = await memory_dashboard_service.get_dashboard_yesterday_changes_async(
                        db=db,
                        workspace_id=workspace_id,
                        storage_type=storage_type,
                        today_data=neo4j_data,
                    )
                    neo4j_data.update(changes)
                except Exception as e:
                    api_logger.warning(f"[V1] 计算neo4j昨日对比失败: {str(e)}")
                    neo4j_data.update({
                        "total_memory_change": None,
                        "total_app_change": None,
                        "total_knowledge_change": None,
                        "total_api_call_change": None,
                    })

                result["neo4j_data"] = neo4j_data

            elif storage_type == "rag":
                rag_data: dict[str, int | None] = {
                    "total_memory": None,
                    "total_app": None,
                    "total_knowledge": None,
                    "total_api_call": None,
                }

                # 1) 记忆总量：rag 独有口径，document 表的 chunk_num 之和
                try:
                    rag_data["total_memory"] = (
                        await memory_dashboard_service.get_rag_user_kb_total_chunk_async(
                            db, current_user
                        )
                    )
                    api_logger.info(f"[V1] 成功获取RAG记忆总量: {rag_data['total_memory']}")
                except Exception as e:
                    api_logger.warning(f"[V1] 获取RAG记忆总量失败: {str(e)}")

                # 2) 共享统计（total_app、total_knowledge、total_api_call）
                common_stats = await memory_dashboard_service.get_dashboard_common_stats_async(
                    db, workspace_id
                )
                rag_data.update(common_stats)

                # 3) 昨日对比
                try:
                    changes = await memory_dashboard_service.get_dashboard_yesterday_changes_async(
                        db=db,
                        workspace_id=workspace_id,
                        storage_type=storage_type,
                        today_data=rag_data,
                    )
                    rag_data.update(changes)
                except Exception as e:
                    api_logger.warning(f"[V1] 计算RAG昨日对比失败: {str(e)}")
                    rag_data.update({
                        "total_memory_change": None,
                        "total_app_change": None,
                        "total_knowledge_change": None,
                        "total_api_call_change": None,
                    })

                result["rag_data"] = rag_data

        except Exception as e:
            api_logger.error(f"[V1] 获取dashboard整合数据失败: {str(e)}")
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content=fail(BizCode.INTERNAL_ERROR, "获取dashboard整合数据失败"),
            )

    return success(data=result, msg="Dashboard数据获取成功")
