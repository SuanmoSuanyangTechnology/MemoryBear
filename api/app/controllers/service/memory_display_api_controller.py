"""记忆展示 服务接口 基于 API Key 认证

提供基于 API Key 认证的对外服务。written / retrieved / engines 三个单接口直接
调用对应的 service 层（与 manager 侧 /api 共用同一批 service 函数）；/all 暂仍
委托 memory_display_controller 的聚合逻辑。

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
from app.core.error_codes import BizCode
from app.core.language_utils import get_language_from_header
from app.core.logging_config import get_business_logger
from app.core.response_utils import fail, success
from app.core.utils.datetime_utils import resolve_iana_timezone
from app.db import get_async_db_context
from app.schemas.api_key_schema import ApiKeyAuth
from app.schemas.response_schema import ApiResponse, PageData, PageMeta
from app.services.memory_display_record_service import MemoryDisplayRecordService
from app.services.memory_engine_display_service import MemoryEngineDisplayService
from app.services.memory_retrieval_display_service import MemoryRetrievalDisplayService

router = APIRouter(prefix="/memory-display", tags=["V1 - Memory Display API"])
logger = get_business_logger()


def _encode_result(result):
    """Encode result for JSON serialization, preserving Response objects as-is."""
    if isinstance(result, Response):
        return result
    return jsonable_encoder(result)


def _time_window_error(start_time: Optional[int], end_time: Optional[int]):
    """校验时间窗：start_time > end_time 时返回 fail 响应，否则返回 None。"""
    if start_time is not None and end_time is not None and start_time > end_time:
        return fail(
            BizCode.INVALID_PARAMETER,
            "start_time 不能大于 end_time",
            f"start={start_time}, end={end_time}",
        )
    return None


# ==================== 写入展示记录 ====================


@router.get("/written", response_model=ApiResponse)
@require_api_key_self_db(scopes=["memory"])
async def get_written_memories(
    request: Request,
    end_user_id: Optional[str] = Query(None, description="终端用户 ID；不传则查询整个工作空间"),
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    pagesize: int = Query(10, ge=1, le=100, description="每页数量"),
    start_time: Optional[int] = Query(None, description="起始时间（毫秒 UTC，闭区间）"),
    end_time: Optional[int] = Query(None, description="结束时间（毫秒 UTC，闭区间）"),
    api_key_auth: ApiKeyAuth = None,
):
    """获取写入展示记录列表

    - 传 end_user_id：返回指定用户的写入记忆展示记录；
    - 不传 end_user_id：返回 API Key 所属工作空间的全部写入记录（空间级），
      每项额外携带 end_user_id 字段区分归属。

    统一按 occurred_at 倒序分页。memory_type 始终返回稳定英文枚举，由前端
    负责展示文案映射；name 和 content 保持记忆生成时的原始语言，不受
    X-Language-Type 影响。
    """
    window_error = _time_window_error(start_time, end_time)
    if window_error is not None:
        return _encode_result(window_error)

    workspace_id = api_key_auth.workspace_id
    logger.info(
        f"V1 get written memories - workspace: {workspace_id}, "
        f"end_user_id: {end_user_id or '(workspace-level)'}"
    )

    async with get_async_db_context() as db:
        resolved_end_user_id = None
        if end_user_id is not None:
            # 合并路由：end_user 可能是合并目标（原 ID 已被合并且 is_active=False），
            # 必须改用 end_user.id，否则下游会按已被合并掉的旧 ID 查询而拿到空数据。
            end_user = await validate_end_user_in_workspace_async(db, end_user_id, workspace_id)
            resolved_end_user_id = end_user.id
        query_result = await MemoryDisplayRecordService.query_written(
            db=db,
            workspace_id=workspace_id,
            page=page,
            pagesize=pagesize,
            end_user_id=resolved_end_user_id,
            start_time=start_time,
            end_time=end_time,
        )

    if query_result is None:
        return _encode_result(fail(
            BizCode.USER_NOT_FOUND,
            "终端用户不存在",
            "end_user not found in current workspace",
        ))
    result_items, total = query_result

    page_meta = PageMeta(
        page=page,
        pagesize=pagesize,
        total=total,
        hasnext=(page * pagesize < total),
    )
    return _encode_result(success(
        data=PageData(page=page_meta, items=result_items),
        msg="查询成功",
    ))


# ==================== 读取展示记录 ====================


@router.get("/retrieved", response_model=ApiResponse)
@require_api_key_self_db(scopes=["memory"])
async def get_retrieved_memories(
    request: Request,
    end_user_id: Optional[str] = Query(None, description="终端用户 ID；不传则查询整个工作空间"),
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    pagesize: int = Query(10, ge=1, le=100, description="每页数量"),
    start_time: Optional[int] = Query(None, description="起始时间（毫秒 UTC，闭区间）"),
    end_time: Optional[int] = Query(None, description="结束时间（毫秒 UTC，闭区间）"),
    api_key_auth: ApiKeyAuth = None,
):
    """获取读取展示记录列表。

    - 传 end_user_id：返回指定用户的读取展示卡片；
    - 不传 end_user_id：返回 API Key 所属工作空间的全部读取卡片（空间级），
      每项额外携带 end_user_id 字段区分归属。
    """
    window_error = _time_window_error(start_time, end_time)
    if window_error is not None:
        return _encode_result(window_error)

    workspace_id = api_key_auth.workspace_id
    logger.info(
        f"V1 get retrieved memories - workspace: {workspace_id}, "
        f"end_user_id: {end_user_id or '(workspace-level)'}"
    )

    async with get_async_db_context() as db:
        resolved_end_user_id = None
        if end_user_id is not None:
            # 合并路由：end_user 可能是合并目标（原 ID 已被合并且 is_active=False），
            # 必须改用 end_user.id，否则下游会按已被合并掉的旧 ID 查询而拿到空数据。
            end_user = await validate_end_user_in_workspace_async(db, end_user_id, workspace_id)
            resolved_end_user_id = end_user.id
        query_result = await MemoryRetrievalDisplayService.query_retrieved(
            db=db,
            workspace_id=workspace_id,
            page=page,
            pagesize=pagesize,
            end_user_id=resolved_end_user_id,
            start_time=start_time,
            end_time=end_time,
        )

    if query_result is None:
        return _encode_result(fail(
            BizCode.USER_NOT_FOUND,
            "终端用户不存在",
            "end_user not found in current workspace",
        ))
    result_items, total = query_result

    page_meta = PageMeta(
        page=page,
        pagesize=pagesize,
        total=total,
        hasnext=(page * pagesize < total),
    )
    return _encode_result(success(
        data=PageData(page=page_meta, items=result_items),
        msg="查询成功",
    ))


# ==================== 引擎动态展示卡片 ====================


@router.get("/engines", response_model=ApiResponse)
@require_api_key_self_db(scopes=["memory"])
async def get_engine_display_cards(
    request: Request,
    timezone: str = Header(
        ...,
        alias="X-Timezone",
        description="IANA 时区名称，前端必传 useI18n().timeZone，如 Asia/Shanghai",
    ),
    end_user_id: Optional[str] = Query(None, description="终端用户 ID；不传则查询整个工作空间"),
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    pagesize: int = Query(10, ge=1, le=100, description="每页数量"),
    start_time: Optional[int] = Query(None, description="起始时间（毫秒 UTC，闭区间）"),
    end_time: Optional[int] = Query(None, description="结束时间（毫秒 UTC，闭区间）"),
    language_type: Optional[str] = Header(None, alias="X-Language-Type"),
    api_key_auth: ApiKeyAuth = None,
):
    """获取引擎动态展示卡片列表

    - 传 end_user_id：返回指定用户的引擎卡片；
    - 不传 end_user_id：返回 API Key 所属工作空间的全部引擎卡片（空间级，
      仍按用户维度拆分），每张卡片额外携带 end_user_id。

    按"指定时区下的自然日 + 引擎类型"聚合事件并返回卡片。engine_type 始终返回
    EXTRACTION、CROSS_MODAL 或 EMOTION，由前端负责展示文案映射；X-Language-Type
    仅控制 name/content 文案。

    聚合边界必须在服务端确定，因此 X-Timezone 为必传请求头，
    前端统一传全局时区设置（useI18n().timeZone），
    保证卡片的聚合日期与前端展示 occurred_at 时使用的时区一致。
    """
    window_error = _time_window_error(start_time, end_time)
    if window_error is not None:
        return _encode_result(window_error)

    # 验证时区请求头（必传，非法值直接报错，避免按错误时区聚合）
    if not timezone or not timezone.strip():
        return _encode_result(fail(
            BizCode.MISSING_PARAMETER,
            "X-Timezone 不能为空",
            "X-Timezone header is required",
        ))
    try:
        _, tz_name = resolve_iana_timezone(timezone)
    except ValueError as e:
        return _encode_result(fail(
            BizCode.INVALID_PARAMETER,
            f"无效的时区: {timezone.strip()}",
            str(e),
        ))

    workspace_id = api_key_auth.workspace_id
    logger.info(
        f"V1 get engine display cards - workspace: {workspace_id}, "
        f"end_user_id: {end_user_id or '(workspace-level)'}"
    )

    language = get_language_from_header(language_type)
    async with get_async_db_context() as db:
        resolved_end_user_id = None
        if end_user_id is not None:
            # 合并路由：end_user 可能是合并目标（原 ID 已被合并且 is_active=False），
            # 必须改用 end_user.id，否则下游会按已被合并掉的旧 ID 查询而拿到空数据。
            end_user = await validate_end_user_in_workspace_async(db, end_user_id, workspace_id)
            resolved_end_user_id = end_user.id
        query_result = await MemoryEngineDisplayService.query_cards(
            db=db,
            workspace_id=workspace_id,
            timezone=tz_name,
            language=language,
            page=page,
            pagesize=pagesize,
            end_user_id=resolved_end_user_id,
            start_time=start_time,
            end_time=end_time,
        )

    if query_result is None:
        return _encode_result(fail(
            BizCode.USER_NOT_FOUND,
            "终端用户不存在",
            "end_user not found in current workspace",
        ))
    cards, total = query_result

    page_meta = PageMeta(
        page=page,
        pagesize=pagesize,
        total=total,
        hasnext=(page * pagesize < total),
    )
    return _encode_result(success(
        data=PageData(page=page_meta, items=cards),
        msg="查询成功",
    ))


# ==================== 全部展示记录 ====================


@router.get("/all")
@require_api_key_self_db(scopes=["memory"])
async def get_all_memory_display(
    request: Request,
    timezone: str = Header(
        ...,
        alias="X-Timezone",
        description="IANA 时区名称，即使 include_engines=false 也必传",
    ),
    end_user_id: Optional[str] = Query(None, description="终端用户 ID；不传则查询整个工作空间"),
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    pagesize: int = Query(10, ge=1, le=100, description="每页数量"),
    include_engines: bool = Query(True, description="是否包含引擎动态卡片"),
    start_time: Optional[int] = Query(None, description="起始时间（毫秒 UTC，闭区间）"),
    end_time: Optional[int] = Query(None, description="结束时间（毫秒 UTC，闭区间）"),
    language_type: Optional[str] = Header(None, alias="X-Language-Type"),
    api_key_auth: ApiKeyAuth = None,
):
    """获取写入、读取和引擎动态的统一时间线。

    - 传 end_user_id：指定用户的统一时间线；
    - 不传 end_user_id：API Key 所属工作空间的统一时间线（空间级），
      每项额外携带 end_user_id 区分归属。
    """
    resolved_end_user_id = None
    async with get_async_db_context() as db:
        current_user = await get_current_user_snapshot_from_api_key_async(db, api_key_auth)
        if end_user_id is not None:
            end_user = await validate_end_user_in_workspace_async(
                db, end_user_id, api_key_auth.workspace_id
            )
            # 合并路由：end_user 可能是合并目标（原 ID 已被合并且 is_active=False），
            # 必须改用 end_user.id，否则下游会按已被合并掉的旧 ID 查询而拿到空数据。
            resolved_end_user_id = str(end_user.id)

    logger.info(
        f"V1 get all memory display - workspace: {api_key_auth.workspace_id}, "
        f"end_user_id: {resolved_end_user_id or '(workspace-level)'}"
    )

    async with get_async_db_context() as db:
        result = await memory_display_controller.get_all_memory_display(
            end_user_id=resolved_end_user_id,
            timezone=timezone,
            page=page,
            pagesize=pagesize,
            include_engines=include_engines,
            start_time=start_time,
            end_time=end_time,
            language_type=language_type,
            current_user=current_user,
            db=db,
        )
    return _encode_result(result)
