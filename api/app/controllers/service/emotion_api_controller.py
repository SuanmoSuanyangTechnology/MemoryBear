"""Emotion Stats 服务接口 — 基于 API Key 认证

对外提供终端用户情绪统计查询（供业务方在自己的产品中向终端用户展示）：
1./query_emotion_overview - 情绪数据概览（最近2个活跃日 + 数据质量 + 核心结论）
2./query_emotion_timeline - 情绪时间轴（分页返回全部活跃日）

复用管理端 Service 层（EmotionStatsService），仅做认证与 workspace 隔离。
GET 请求：参数经 Query 传递，切日基准由必传 X-Timezone 请求头指定；
慢于 UTC+8 的时区查询前触发实时补数（失败降级不阻塞）。

路由前缀: /memory/emotion-memory
最终路径: /v1/memory/emotion-memory/query_emotion_overview | /query_emotion_timeline
认证方式: API Key (@require_api_key_self_db)
"""

from fastapi import APIRouter, Depends, Header, Request

from app.core.api_key_auth import (
    require_api_key_self_db,
    get_current_api_key_auth,
)
from app.core.api_key_utils import validate_end_user_in_workspace_async
from app.core.error_codes import BizCode
from app.core.logging_config import get_business_logger
from app.db import get_async_db_context, get_db_context
from app.schemas.emotion_schema import (
    EmotionDailyOverviewRequest,
    EmotionDailyTimelineRequest,
)
from app.core.response_utils import fail, success
from app.schemas.response_schema import ApiResponse
from app.services.emotion_stats_service import EmotionStatsService
from app.core.language_utils import get_language_from_header
from app.core.utils.datetime_utils import resolve_iana_timezone

router = APIRouter(prefix="/memory/emotion-memory", tags=["V1 - Emotion Stats API"])
logger = get_business_logger()


def _resolve_timezone_or_fail(timezone: str | None):
    """校验 X-Timezone 请求头（必传，非法值直接报错，避免按错误时区聚合）

    Returns:
        (tz_name, error_response)：二者互斥，校验失败时 tz_name 为 None
    """
    if not timezone or not timezone.strip():
        return None, fail(
            BizCode.MISSING_PARAMETER,
            "X-Timezone 不能为空",
            "X-Timezone header is required",
        )
    try:
        _, tz_name = resolve_iana_timezone(timezone)
    except ValueError as e:
        return None, fail(
            BizCode.INVALID_PARAMETER,
            f"无效的时区: {timezone.strip()}",
            str(e),
        )
    return tz_name, None


@router.get("/query_emotion_overview", response_model=ApiResponse)
@require_api_key_self_db(scopes=["memory"])
async def query_emotion_overview(
    request: Request,
    params: EmotionDailyOverviewRequest = Depends(),
    timezone: str = Header(..., alias="X-Timezone"),
    language_type: str = Header(default=None, alias="X-Language-Type"),
):
    """获取情绪数据概览（最近2个活跃日 + 数据质量 + 核心结论）

    读取 PG dialogue_emotion_raw 表（dialogue 粒度），按 X-Timezone 实时切日聚合；
    end_user 必须属于 API Key 绑定的 workspace。
    注意：首参必须为 `request: Request`（API Key 装饰器约定），Query 参数模型用 `params`。

    Returns:
        data_quality / summary / conclusion / items
    """
    tz_name, error_response = _resolve_timezone_or_fail(timezone)
    if error_response is not None:
        return error_response

    api_key_auth = get_current_api_key_auth()
    # 1. workspace 归属校验（异步 session）
    async with get_async_db_context() as auth_db:
        await validate_end_user_in_workspace_async(
            auth_db, params.end_user_id, api_key_auth.workspace_id
        )

    # 2. 慢时区实时补数（内部三层短路 + 失败降级，不阻塞查询）
    await EmotionStatsService.backfill_for_timezone(params.end_user_id, tz_name)

    # 3. 复用管理端 Service（内部为同步查询，用同步 session）
    language = get_language_from_header(language_type)
    with get_db_context() as db:
        service = EmotionStatsService(db)
        data = service.query_overview(
            end_user_id=params.end_user_id,
            tz_name=tz_name,
            language=language,
        )

    logger.info(
        "V1 情绪数据概览获取成功",
        extra={
            "workspace_id": str(api_key_auth.workspace_id),
            "end_user_id": params.end_user_id,
            "data_quality": data.get("data_quality"),
        },
    )
    return success(data=data, msg="情绪数据概览获取成功")


@router.get("/query_emotion_timeline", response_model=ApiResponse)
@require_api_key_self_db(scopes=["memory"])
async def query_emotion_timeline(
    request: Request,
    params: EmotionDailyTimelineRequest = Depends(),
    timezone: str = Header(..., alias="X-Timezone"),
    language_type: str = Header(default=None, alias="X-Language-Type"),
):
    """获取情绪时间轴（分页返回全部活跃日）

    读取 PG dialogue_emotion_raw 表（dialogue 粒度），按 X-Timezone 实时切日聚合，
    支持日期范围过滤与排序（断档 gaps 由前端自算，接口不再返回）；
    end_user 必须属于 API Key 绑定的 workspace。
    注意：首参必须为 `request: Request`（API Key 装饰器约定），Query 参数模型用 `params`。

    Returns:
        page(page/pagesize/total/hasnext) / items
    """
    tz_name, error_response = _resolve_timezone_or_fail(timezone)
    if error_response is not None:
        return error_response

    api_key_auth = get_current_api_key_auth()
    async with get_async_db_context() as auth_db:
        await validate_end_user_in_workspace_async(
            auth_db, params.end_user_id, api_key_auth.workspace_id
        )

    # 慢时区实时补数（内部三层短路 + 失败降级，不阻塞查询）
    await EmotionStatsService.backfill_for_timezone(params.end_user_id, tz_name)

    language = get_language_from_header(language_type)
    with get_db_context() as db:
        service = EmotionStatsService(db)
        data = service.query_timeline(
            end_user_id=params.end_user_id,
            tz_name=tz_name,
            start_date=params.start_date,
            end_date=params.end_date,
            sort=params.sort,
            page=params.page,
            page_size=params.pagesize,
            language=language,
        )

    logger.info(
        "V1 情绪时间轴获取成功",
        extra={
            "workspace_id": str(api_key_auth.workspace_id),
            "end_user_id": params.end_user_id,
            "total": data.get("page", {}).get("total"),
        },
    )
    return success(data=data, msg="情绪时间轴获取成功")
