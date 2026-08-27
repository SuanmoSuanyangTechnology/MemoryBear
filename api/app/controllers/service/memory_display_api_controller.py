"""记忆展示 服务接口 基于 API Key 认证

包装 memory_display_controller.py 中的内部接口，提供基于 API Key 认证的对外服务

路由前缀: /memory/memory-display
最终路径: /v1/memory/memory-display/...
认证方式: API Key (@require_api_key)
"""

from typing import Optional

from fastapi import APIRouter, Header, Query, Request
from fastapi.encoders import jsonable_encoder
from starlette.responses import Response

from app.controllers import memory_display_controller
from app.core.api_key_auth import require_api_key_self_db
from app.core.api_key_utils import (
    get_current_user_snapshot_from_api_key_async,
    validate_end_user_in_workspace_async,
)
from app.core.logging_config import get_business_logger
from app.db import get_async_db_context
from app.schemas.api_key_schema import ApiKeyAuth

router = APIRouter(prefix="/memory-display", tags=["V1 - Memory Display API"])
logger = get_business_logger()


def _encode_result(result):
    """Encode result for JSON serialization, preserving Response objects as-is."""
    if isinstance(result, Response):
        return result
    return jsonable_encoder(result)


# ==================== 写入展示记录 ====================


@router.get("/written")
@require_api_key_self_db(scopes=["memory"])
async def get_written_memories(
    request: Request,
    end_user_id: str = Query(..., description="终端用户 ID"),
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    pagesize: int = Query(10, ge=1, le=100, description="每页数量"),
    api_key_auth: ApiKeyAuth = None,
):
    """获取写入展示记录列表

    返回指定用户的写入记忆展示记录，按 occurred_at 倒序分页。

    memory_type 始终返回稳定英文枚举，由前端负责展示文案映射；name 和
    content 保持记忆生成时的原始语言，不受 X-Language-Type 影响。
    """
    async with get_async_db_context() as db:
        current_user = await get_current_user_snapshot_from_api_key_async(db, api_key_auth)
        end_user = await validate_end_user_in_workspace_async(db, end_user_id, api_key_auth.workspace_id)
        # 合并路由：end_user 可能是合并目标（原 ID 已被合并且 is_active=False），
        # 必须改用 end_user.id，否则下游会按已被合并掉的旧 ID 查询而拿到空数据。
        end_user_id = str(end_user.id)

    logger.info(f"V1 get written memories - workspace: {api_key_auth.workspace_id}")

    async with get_async_db_context() as db:
        result = await memory_display_controller.get_written_memories(
            end_user_id=end_user_id,
            page=page,
            pagesize=pagesize,
            current_user=current_user,
            db=db,
        )
    return _encode_result(result)


# ==================== 读取展示记录 ====================


@router.get("/retrieved")
@require_api_key_self_db(scopes=["memory"])
async def get_retrieved_memories(
    request: Request,
    end_user_id: str = Query(..., description="终端用户 ID"),
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    pagesize: int = Query(10, ge=1, le=100, description="每页数量"),
    api_key_auth: ApiKeyAuth = None,
):
    """获取读取展示记录列表。"""
    async with get_async_db_context() as db:
        current_user = await get_current_user_snapshot_from_api_key_async(db, api_key_auth)
        end_user = await validate_end_user_in_workspace_async(db, end_user_id, api_key_auth.workspace_id)
        # 合并路由：end_user 可能是合并目标（原 ID 已被合并且 is_active=False），
        # 必须改用 end_user.id，否则下游会按已被合并掉的旧 ID 查询而拿到空数据。
        end_user_id = str(end_user.id)

    logger.info(f"V1 get retrieved memories - workspace: {api_key_auth.workspace_id}")

    async with get_async_db_context() as db:
        result = await memory_display_controller.get_retrieved_memories(
            end_user_id=end_user_id,
            page=page,
            pagesize=pagesize,
            current_user=current_user,
            db=db,
        )
    return _encode_result(result)


# ==================== 引擎动态展示卡片 ====================


@router.get("/engines")
@require_api_key_self_db(scopes=["memory"])
async def get_engine_display_cards(
    request: Request,
    end_user_id: str = Query(..., description="终端用户 ID"),
    timezone: str = Header(
        ...,
        alias="X-Timezone",
        description="IANA 时区名称，前端必传 useI18n().timeZone，如 Asia/Shanghai",
    ),
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    pagesize: int = Query(10, ge=1, le=100, description="每页数量"),
    language_type: Optional[str] = Header(None, alias="X-Language-Type"),
    api_key_auth: ApiKeyAuth = None,
):
    """获取引擎动态展示卡片列表

    按"指定时区下的自然日 + 引擎类型"聚合事件并返回卡片。

    engine_type 始终返回 EXTRACTION、CROSS_MODAL 或 EMOTION，
    由前端负责展示文案映射；X-Language-Type 仅控制 name/content 文案。

    聚合边界必须在服务端确定，因此 X-Timezone 为必传请求头，
    前端统一传全局时区设置（useI18n().timeZone），
    保证卡片的聚合日期与前端展示 occurred_at 时使用的时区一致。
    """
    async with get_async_db_context() as db:
        current_user = await get_current_user_snapshot_from_api_key_async(db, api_key_auth)
        end_user = await validate_end_user_in_workspace_async(db, end_user_id, api_key_auth.workspace_id)
        # 合并路由：end_user 可能是合并目标（原 ID 已被合并且 is_active=False），
        # 必须改用 end_user.id，否则下游会按已被合并掉的旧 ID 查询而拿到空数据。
        end_user_id = str(end_user.id)

    logger.info(f"V1 get engine display cards - workspace: {api_key_auth.workspace_id}")

    async with get_async_db_context() as db:
        result = await memory_display_controller.get_engine_display_cards(
            end_user_id=end_user_id,
            timezone=timezone,
            page=page,
            pagesize=pagesize,
            language_type=language_type,
            current_user=current_user,
            db=db,
        )
    return _encode_result(result)


# ==================== 全部展示记录 ====================


@router.get("/all")
@require_api_key_self_db(scopes=["memory"])
async def get_all_memory_display(
    request: Request,
    end_user_id: str = Query(..., description="终端用户 ID"),
    timezone: str = Header(
        ...,
        alias="X-Timezone",
        description="IANA 时区名称，即使 include_engines=false 也必传",
    ),
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    pagesize: int = Query(10, ge=1, le=100, description="每页数量"),
    include_engines: bool = Query(True, description="是否包含引擎动态卡片"),
    language_type: Optional[str] = Header(None, alias="X-Language-Type"),
    api_key_auth: ApiKeyAuth = None,
):
    """获取写入、读取和引擎动态的统一时间线。"""
    async with get_async_db_context() as db:
        current_user = await get_current_user_snapshot_from_api_key_async(db, api_key_auth)
        end_user = await validate_end_user_in_workspace_async(db, end_user_id, api_key_auth.workspace_id)
        # 合并路由：end_user 可能是合并目标（原 ID 已被合并且 is_active=False），
        # 必须改用 end_user.id，否则下游会按已被合并掉的旧 ID 查询而拿到空数据。
        end_user_id = str(end_user.id)

    logger.info(f"V1 get all memory display - workspace: {api_key_auth.workspace_id}")

    async with get_async_db_context() as db:
        result = await memory_display_controller.get_all_memory_display(
            end_user_id=end_user_id,
            timezone=timezone,
            page=page,
            pagesize=pagesize,
            include_engines=include_engines,
            language_type=language_type,
            current_user=current_user,
            db=db,
        )
    return _encode_result(result)
