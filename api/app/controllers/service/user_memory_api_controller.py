"""User Memory 服务接口 — 基于 API Key 认证（纯异步版本）

包装 user_memory_controllers.py 和 memory_agent_controller.py 中的内部接口，
提供基于 API Key 认证的对外服务:
1./analytics/graph_data          - 知识图谱数据接口
2./analytics/community_graph     - 社区图谱接口
3./analytics/node_statistics     - 记忆节点统计接口
4./analytics/user_summary        - 用户摘要接口
5./analytics/memory_insight      - 记忆洞察接口
6./analytics/interest_distribution - 兴趣分布接口
7./analytics/end_user_info       - 终端用户信息接口
8./analytics/generate_cache      - 缓存生成接口

路由前缀: /memory
子路径: /analytics/...
最终路径: /v1/memory/analytics/...
认证方式: API Key（``@require_api_key_self_db``，装饰器内部走 async DB 完成认证 + 限流）
"""

from typing import Optional

from fastapi import APIRouter, Body, Header, Query, Request

from app.controllers import end_user_controller, memory_analytics_controller
from app.core.api_key_auth import require_api_key_self_db
from app.core.api_key_utils import (
    get_current_user_from_api_key_async,
    validate_end_user_in_workspace_async,
)
from app.core.logging_config import get_business_logger
from app.db import get_async_db_context
from app.dependencies import CurrentUserSnapshot
from app.schemas.api_key_schema import ApiKeyAuth
from app.schemas.memory_storage_schema import GenerateCacheRequest

router = APIRouter(prefix="/memory", tags=["V1 - User Memory API"])
logger = get_business_logger()


async def _resolve_user_and_validate_end_user(api_key_auth: ApiKeyAuth, end_user_id: str):
    """异步完成 API Key 用户解析 + end_user 归属校验，返回 CurrentUserSnapshot。

    在 session 关闭前将所有 User 属性提取为纯数据快照，避免返回 detached ORM 对象
    导致后续访问属性时触发 DetachedInstanceError。
    """
    async with get_async_db_context() as db:
        current_user = await get_current_user_from_api_key_async(db, api_key_auth)
        await validate_end_user_in_workspace_async(db, end_user_id, api_key_auth.workspace_id)
        return CurrentUserSnapshot(
            id=current_user.id,
            username=current_user.username,
            email=current_user.email,
            is_active=current_user.is_active,
            is_superuser=current_user.is_superuser,
            current_workspace_id=current_user.current_workspace_id,
            tenant_id=current_user.tenant_id,
        )


# ==================== 知识图谱 ====================


@router.get("/analytics/graph_data")
@require_api_key_self_db(scopes=["memory"])
async def get_graph_data(
    request: Request,
    end_user_id: str = Query(..., description="End user ID"),
    node_types: Optional[str] = Query(None, description="Comma-separated node types filter"),
    limit: int = Query(100, description="Max nodes to return (auto-capped at 1000 in service layer)"),
    depth: int = Query(1, description="Graph traversal depth (auto-capped at 3 in service layer)"),
    center_node_id: Optional[str] = Query(None, description="Center node for subgraph"),
    api_key_auth: ApiKeyAuth = None,
):
    """Get knowledge graph data (nodes + edges) for an end user."""
    current_user = await _resolve_user_and_validate_end_user(api_key_auth, end_user_id)

    return await memory_analytics_controller.get_graph_data_api(
        end_user_id=end_user_id,
        node_types=node_types,
        limit=limit,
        depth=depth,
        center_node_id=center_node_id,
        current_user=current_user,
    )


@router.get("/analytics/community_graph")
@require_api_key_self_db(scopes=["memory"])
async def get_community_graph(
    request: Request,
    # HACK （end_user_id数据类型修改）改成end_user_uuid: UUID
    end_user_id: str = Query(..., description="End user ID"),
    api_key_auth: ApiKeyAuth = None,
):
    """Get community clustering graph for an end user."""
    current_user = await _resolve_user_and_validate_end_user(api_key_auth, end_user_id)

    return await memory_analytics_controller.get_community_graph_data_api(
        end_user_id=end_user_id,
        current_user=current_user,
    )


# ==================== 节点统计 ====================


@router.get("/analytics/node_statistics")
@require_api_key_self_db(scopes=["memory"])
async def get_node_statistics(
    request: Request,
    end_user_id: str = Query(..., description="End user ID"),
    api_key_auth: ApiKeyAuth = None,
):
    """Get memory node type statistics for an end user."""
    current_user = await _resolve_user_and_validate_end_user(api_key_auth, end_user_id)

    return await memory_analytics_controller.get_node_statistics_api(
        end_user_id=end_user_id,
        current_user=current_user,
    )


# ==================== 用户摘要 & 洞察 ====================


@router.get("/analytics/user_summary")
@require_api_key_self_db(scopes=["memory"])
async def get_user_summary(
    request: Request,
    end_user_id: str = Query(..., description="End user ID"),
    language_type: str = Header(default=None, alias="X-Language-Type"),
    api_key_auth: ApiKeyAuth = None,
):
    """Get cached user summary for an end user."""
    current_user = await _resolve_user_and_validate_end_user(api_key_auth, end_user_id)

    return await memory_analytics_controller.get_user_summary_api(
        end_user_id=end_user_id,
        language_type=language_type,
        current_user=current_user,
    )


@router.get("/analytics/memory_insight")
@require_api_key_self_db(scopes=["memory"])
async def get_memory_insight(
    request: Request,
    end_user_id: str = Query(..., description="End user ID"),
    api_key_auth: ApiKeyAuth = None,
):
    """Get cached memory insight report for an end user."""
    current_user = await _resolve_user_and_validate_end_user(api_key_auth, end_user_id)

    return await memory_analytics_controller.get_memory_insight_report_api(
        end_user_id=end_user_id,
        current_user=current_user,
    )


# ==================== 兴趣分布 ====================


@router.get("/analytics/interest_distribution")
@require_api_key_self_db(scopes=["memory"])
async def get_interest_distribution(
    request: Request,
    end_user_id: str = Query(..., description="End user ID"),
    limit: int = Query(5, le=5, description="Max interest tags to return"),
    language_type: str = Header(default=None, alias="X-Language-Type"),
    api_key_auth: ApiKeyAuth = None,
):
    """Get interest distribution tags for an end user."""
    current_user = await _resolve_user_and_validate_end_user(api_key_auth, end_user_id)

    return await memory_analytics_controller.get_interest_distribution_by_user_api(
        end_user_id=end_user_id,
        limit=limit,
        language_type=language_type,
        current_user=current_user,
    )


# ==================== 终端用户信息 ====================


@router.get("/analytics/end_user_info")
@require_api_key_self_db(scopes=["memory"])
async def get_end_user_info(
    request: Request,
    end_user_id: str = Query(..., description="End user ID"),
    api_key_auth: ApiKeyAuth = None,
):
    """Get end user basic information (name, aliases, metadata)."""
    current_user = await _resolve_user_and_validate_end_user(api_key_auth, end_user_id)

    return await end_user_controller.get_end_user_info(
        end_user_id=end_user_id,
        current_user=current_user,
    )


# ==================== 缓存生成 ====================


@router.post("/analytics/generate_cache")
@require_api_key_self_db(scopes=["memory"])
async def generate_cache(
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    message: str = Body(None, description="Request body"),
    language_type: str = Header(default=None, alias="X-Language-Type"),
):
    """Trigger cache generation (user summary + memory insight) for an end user or all workspace users."""
    body = await request.json()
    cache_request = GenerateCacheRequest(**body)

    async with get_async_db_context() as db:
        current_user = await get_current_user_from_api_key_async(db, api_key_auth)
        if cache_request.end_user_id:
            await validate_end_user_in_workspace_async(
                db, cache_request.end_user_id, api_key_auth.workspace_id
            )

    return await memory_analytics_controller.generate_cache_api(
        request=cache_request,
        language_type=language_type,
        current_user=current_user,
    )
