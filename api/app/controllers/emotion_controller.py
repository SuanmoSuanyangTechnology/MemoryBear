# -*- coding: utf-8 -*-
"""情绪分析控制器模块

本模块提供情绪分析相关的API端点，包括情绪标签、词云、健康指数和个性化建议。

Routes:
    POST /emotion/tags - 获取情绪标签统计
    POST /emotion/wordcloud - 获取情绪词云数据
    POST /emotion/health - 获取情绪健康指数
    POST /emotion/suggestions - 获取个性化情绪建议
"""

from app.core.error_codes import BizCode
from app.core.language_utils import get_language_from_header
from app.core.logging_config import get_api_logger
from app.core.response_utils import fail, success
from app.dependencies import get_current_user, get_db
from app.models.user_model import User
from app.schemas.emotion_schema import (
EmotionDailyOverviewRequest,
    EmotionDailyTimelineRequest,
    EmotionHealthRequest,
    EmotionSuggestionsRequest,
    EmotionGenerateSuggestionsRequest,
    EmotionTagsRequest,
    EmotionWordcloudRequest,
)
from app.schemas.response_schema import ApiResponse
from app.services.emotion_analytics_service import EmotionAnalyticsService
from app.services.emotion_stats_service import EmotionStatsService
from app.core.utils.datetime_utils import resolve_iana_timezone
from fastapi import APIRouter, Depends, HTTPException, status,Header
from sqlalchemy.orm import Session

# 获取API专用日志器
api_logger = get_api_logger()

router = APIRouter(
    prefix="/memory/emotion-memory",
    tags=["Emotion Analysis"],
    dependencies=[Depends(get_current_user)]  # 所有路由都需要认证
)


# 初始化情绪分析服务uv
emotion_service = EmotionAnalyticsService()



@router.post("/tags", response_model=ApiResponse)
async def get_emotion_tags(
    request: EmotionTagsRequest,
    language_type: str = Header(default=None, alias="X-Language-Type"),
    current_user: User = Depends(get_current_user),
):

    try:
        # 使用集中化的语言校验
        language = get_language_from_header(language_type)
        
        api_logger.info(
            f"用户 {current_user.username} 请求获取情绪标签统计",
            extra={
                "end_user_id": request.end_user_id,
                "emotion_type": request.emotion_type,
                "start_date": request.start_date,
                "end_date": request.end_date,
                "limit": request.limit,
                "language_type": language
            }
        )

        # 调用服务层
        data = await emotion_service.get_emotion_tags(
            end_user_id=request.end_user_id,
            emotion_type=request.emotion_type,
            start_date=request.start_date,
            end_date=request.end_date,
            limit=request.limit,
            language=language
        )

        api_logger.info(
            "情绪标签统计获取成功",
            extra={
                "end_user_id": request.end_user_id,
                "total_count": data.get("total_count", 0),
                "tags_count": len(data.get("tags", []))
            }
        )

        return success(data=data, msg="情绪标签获取成功")

    except Exception as e:
        api_logger.error(
            f"获取情绪标签统计失败: {str(e)}",
            extra={"end_user_id": request.end_user_id},
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取情绪标签统计失败: {str(e)}"
        )



@router.post("/wordcloud", response_model=ApiResponse)
async def get_emotion_wordcloud(
    request: EmotionWordcloudRequest,
    language_type: str = Header(default=None, alias="X-Language-Type"),
    current_user: User = Depends(get_current_user),
):

    try:
        # 使用集中化的语言校验
        language = get_language_from_header(language_type)
        
        api_logger.info(
            f"用户 {current_user.username} 请求获取情绪词云数据",
            extra={
                "end_user_id": request.end_user_id,
                "emotion_type": request.emotion_type,
                "limit": request.limit
            }
        )

        # 调用服务层
        data = await emotion_service.get_emotion_wordcloud(
            end_user_id=request.end_user_id,
            emotion_type=request.emotion_type,
            limit=request.limit
        )

        api_logger.info(
            "情绪词云数据获取成功",
            extra={
                "end_user_id": request.end_user_id,
                "total_keywords": data.get("total_keywords", 0)
            }
        )

        return success(data=data, msg="情绪词云获取成功")

    except Exception as e:
        api_logger.error(
            f"获取情绪词云数据失败: {str(e)}",
            extra={"end_user_id": request.end_user_id},
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取情绪词云数据失败: {str(e)}"
        )



@router.post("/health", response_model=ApiResponse)
async def get_emotion_health(
    request: EmotionHealthRequest,
    language_type: str = Header(default=None, alias="X-Language-Type"),
    current_user: User = Depends(get_current_user),
):

    try:
        # 使用集中化的语言校验
        language = get_language_from_header(language_type)
        
        # 验证时间范围参数
        if request.time_range not in ["7d", "30d", "90d"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="时间范围参数无效，必须是 7d、30d 或 90d"
            )

        api_logger.info(
            f"用户 {current_user.username} 请求获取情绪健康指数",
            extra={
                "end_user_id": request.end_user_id,
                "time_range": request.time_range
            }
        )

        # 调用服务层
        data = await emotion_service.calculate_emotion_health_index(
            end_user_id=request.end_user_id,
            time_range=request.time_range
        )

        api_logger.info(
            "情绪健康指数获取成功",
            extra={
                "end_user_id": request.end_user_id,
                "health_score": data.get("health_score") or 0,
                "level": data.get("level", "未知")
            }
        )

        return success(data=data, msg="情绪健康指数获取成功")

    except HTTPException:
        raise
    except Exception as e:
        api_logger.error(
            f"获取情绪健康指数失败: {str(e)}",
            extra={"end_user_id": request.end_user_id},
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取情绪健康指数失败: {str(e)}"
        )



# @router.post("/check-data", response_model=ApiResponse)
# async def check_emotion_data_exists(
#     request: EmotionSuggestionsRequest,
#     db: Session = Depends(get_db),
#     current_user: User = Depends(get_current_user),
# ):
#     """检查用户情绪建议数据是否存在

#     Args:
#         request: 包含 end_user_id
#         db: 数据库会话
#         current_user: 当前用户

#     Returns:
#         数据存在状态
#     """
#     try:
#         api_logger.info(
#             f"检查用户情绪建议数据是否存在: {request.end_user_id}",
#             extra={"end_user_id": request.end_user_id}
#         )

#         # 从数据库获取建议
#         data = await emotion_service.get_cached_suggestions(
#             end_user_id=request.end_user_id,
#             db=db
#         )

#         if data is None:
#             api_logger.info(f"用户 {request.end_user_id} 的情绪建议数据不存在")
#             return fail(
#                 BizCode.NOT_FOUND,
#                 "情绪建议数据不存在，请点击右上角刷新进行初始化",
#                 {"exists": False}
#             )

#         api_logger.info(f"用户 {request.end_user_id} 的情绪建议数据存在")
#         return success(data={"exists": True}, msg="情绪建议数据已存在")

#     except Exception as e:
#         api_logger.error(
#             f"检查情绪建议数据失败: {str(e)}",
#             extra={"end_user_id": request.end_user_id},
#             exc_info=True
#         )
#         raise HTTPException(
#             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             detail=f"检查情绪建议数据失败: {str(e)}"
#         )


@router.post("/suggestions", response_model=ApiResponse)
async def get_emotion_suggestions(
    request: EmotionSuggestionsRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取个性化情绪建议（从数据库读取）

    Args:
        request: 包含 end_user_id 和可选的 config_id
        db: 数据库会话
        current_user: 当前用户

    Returns:
        存储的个性化情绪建议响应
    """
    try:
        api_logger.info(
            f"用户 {current_user.username} 请求获取个性化情绪建议",
            extra={
                "end_user_id": request.end_user_id,
                "config_id": request.config_id
            }
        )

        # 从数据库获取建议
        data = await emotion_service.get_cached_suggestions(
            end_user_id=request.end_user_id,
            db=db
        )

        if data is None:
            api_logger.info(
                f"用户 {request.end_user_id} 的建议数据不存在",
                extra={"end_user_id": request.end_user_id}
            )
            return success(
                data={"exists": False},
                msg="情绪建议数据不存在，请点击右上角刷新进行初始化"
            )

        api_logger.info(
            "个性化建议获取成功",
            extra={
                "end_user_id": request.end_user_id,
                "suggestions_count": len(data.get("suggestions", []))
            }
        )

        return success(data=data, msg="个性化建议获取成功")

    except Exception as e:
        api_logger.error(
            f"获取个性化建议失败: {str(e)}",
            extra={"end_user_id": request.end_user_id},
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取个性化建议失败: {str(e)}"
        )


@router.post("/generate_suggestions", response_model=ApiResponse)
async def generate_emotion_suggestions(
    request: EmotionGenerateSuggestionsRequest,
    language_type: str = Header(default=None, alias="X-Language-Type"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """生成个性化情绪建议（调用LLM并保存到数据库）

    Args:
        request: 包含 end_user_id
        db: 数据库会话
        current_user: 当前用户

    Returns:
        新生成的个性化情绪建议响应
    """
    try:
        # 使用集中化的语言校验
        language = get_language_from_header(language_type)
        
        api_logger.info(
            f"用户 {current_user.username} 请求生成个性化情绪建议",
            extra={
                "end_user_id": request.end_user_id
            }
        )

        # 调用服务层生成建议
        data = await emotion_service.generate_emotion_suggestions(
            end_user_id=request.end_user_id,
            db=db,
            language=language
        )

        # 保存到数据库
        await emotion_service.save_suggestions_cache(
            end_user_id=request.end_user_id,
            suggestions_data=data,
            db=db
        )

        api_logger.info(
            "个性化建议生成成功",
            extra={
                "end_user_id": request.end_user_id,
                "suggestions_count": len(data.get("suggestions", []))
            }
        )

        return success(data=data, msg="个性化建议生成成功")

    except Exception as e:
        api_logger.error(
            f"生成个性化建议失败: {str(e)}",
            extra={"end_user_id": request.end_user_id},
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"生成个性化建议失败: {str(e)}"
        )


@router.get("/query_emotion_overview", response_model=ApiResponse)
async def query_emotion_overview(
    request: EmotionDailyOverviewRequest = Depends(),
    timezone: str = Header(..., alias="X-Timezone"),
    language_type: str = Header(default=None, alias="X-Language-Type"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取情绪数据概览（最近2个活跃日 + 数据质量 + 核心结论）

    读取 PG dialogue_emotion_raw 表（dialogue 粒度），按 X-Timezone 实时切日聚合；
    慢于 UTC+8 的时区查询前触发实时补数（失败降级不阻塞）。

    Args:
        request: 查询参数（end_user_id，Query 参数）
        timezone: X-Timezone 请求头（必传，IANA 时区名，切日基准）
        language_type: X-Language-Type 请求头（zh/en，决定 display_name 语言）
        db: 数据库会话
        current_user: 当前用户

    Returns:
        data_quality / summary / conclusion / items
    """
    # 验证时区请求头（必传，非法值直接报错，避免按错误时区聚合）
    if not timezone or not timezone.strip():
        return fail(
            BizCode.MISSING_PARAMETER,
            "X-Timezone 不能为空",
            "X-Timezone header is required",
        )
    try:
        _, tz_name = resolve_iana_timezone(timezone)
    except ValueError as e:
        return fail(
            BizCode.INVALID_PARAMETER,
            f"无效的时区: {timezone.strip()}",
            str(e),
        )

    try:
        language = get_language_from_header(language_type)

        api_logger.info(
            f"用户 {current_user.username} 请求获取情绪数据概览",
            extra={"end_user_id": request.end_user_id, "language_type": language}
        )

        # 慢时区实时补数（内部三层短路 + 失败降级，不阻塞查询）
        await EmotionStatsService.backfill_for_timezone(
            request.end_user_id, tz_name
        )

        service = EmotionStatsService(db)
        data = service.query_overview(
            end_user_id=request.end_user_id,
            tz_name=tz_name,
            language=language,
        )

        api_logger.info(
            "情绪数据概览获取成功",
            extra={
                "end_user_id": request.end_user_id,
                "data_quality": data.get("data_quality"),
            }
        )

        return success(data=data, msg="情绪数据概览获取成功")

    except HTTPException:
        raise
    except Exception as e:
        api_logger.error(
            f"获取情绪数据概览失败: {str(e)}",
            extra={"end_user_id": request.end_user_id},
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取情绪数据概览失败: {str(e)}"
        )


@router.get("/query_emotion_timeline", response_model=ApiResponse)
async def query_emotion_timeline(
    request: EmotionDailyTimelineRequest = Depends(),
    timezone: str = Header(..., alias="X-Timezone"),
    language_type: str = Header(default=None, alias="X-Language-Type"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取情绪时间轴（分页返回全部活跃日）

    读取 PG dialogue_emotion_raw 表（dialogue 粒度），按 X-Timezone 实时切日聚合，
    支持日期范围过滤与排序（断档 gaps 由前端自算，接口不再返回）；
    慢于 UTC+8 的时区查询前触发实时补数（失败降级不阻塞）。

    Args:
        request: 查询参数（end_user_id + 可选 start_date/end_date/sort/page/pagesize）
        timezone: X-Timezone 请求头（必传，IANA 时区名，切日基准）
        language_type: X-Language-Type 请求头（zh/en，决定 display_name 语言）
        db: 数据库会话
        current_user: 当前用户

    Returns:
        page(page/pagesize/total/hasnext) / items
    """
    # 验证时区请求头（必传，非法值直接报错，避免按错误时区聚合）
    if not timezone or not timezone.strip():
        return fail(
            BizCode.MISSING_PARAMETER,
            "X-Timezone 不能为空",
            "X-Timezone header is required",
        )
    try:
        _, tz_name = resolve_iana_timezone(timezone)
    except ValueError as e:
        return fail(
            BizCode.INVALID_PARAMETER,
            f"无效的时区: {timezone.strip()}",
            str(e),
        )

    try:
        language = get_language_from_header(language_type)

        api_logger.info(
            f"用户 {current_user.username} 请求获取情绪时间轴",
            extra={
                "end_user_id": request.end_user_id,
                "start_date": request.start_date,
                "end_date": request.end_date,
                "sort": request.sort,
                "page": request.page,
                "pagesize": request.pagesize,
                "language_type": language,
            }
        )

        # 慢时区实时补数（内部三层短路 + 失败降级，不阻塞查询）
        await EmotionStatsService.backfill_for_timezone(
            request.end_user_id, tz_name
        )

        service = EmotionStatsService(db)
        data = service.query_timeline(
            end_user_id=request.end_user_id,
            tz_name=tz_name,
            start_date=request.start_date,
            end_date=request.end_date,
            sort=request.sort,
            page=request.page,
            page_size=request.pagesize,
            language=language,
        )

        api_logger.info(
            "情绪时间轴获取成功",
            extra={
                "end_user_id": request.end_user_id,
                "total": data.get("page", {}).get("total"),
                "page": data.get("page", {}).get("page"),
            }
        )

        return success(data=data, msg="情绪时间轴获取成功")

    except HTTPException:
        raise
    except Exception as e:
        api_logger.error(
            f"获取情绪时间轴失败: {str(e)}",
            extra={"end_user_id": request.end_user_id},
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取情绪时间轴失败: {str(e)}"
        )