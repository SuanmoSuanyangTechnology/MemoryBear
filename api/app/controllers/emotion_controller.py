# -*- coding: utf-8 -*-
"""情绪分析控制器模块

本模块提供情绪分析相关的API端点，包括情绪标签、词云、健康指数和个性化建议。

Routes:
    POST /emotion/tags - 获取情绪标签统计
    POST /emotion/wordcloud - 获取情绪词云数据
    POST /emotion/health - 获取情绪健康指数
    POST /emotion/suggestions - 获取个性化情绪建议
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.response_utils import success, fail
from app.core.error_codes import BizCode
from app.dependencies import get_current_user, get_db
from app.models.user_model import User
from app.schemas.response_schema import ApiResponse
from app.schemas.emotion_schema import (
    EmotionTagsRequest,
    EmotionWordcloudRequest,
    EmotionHealthRequest,
    EmotionSuggestionsRequest
)
from app.services.emotion_analytics_service import EmotionAnalyticsService
from app.core.logging_config import get_api_logger

# 获取API专用日志器
api_logger = get_api_logger()

router = APIRouter(
    prefix="/memory/emotion",
    tags=["Emotion Analysis"],
    dependencies=[Depends(get_current_user)]  # 所有路由都需要认证
)


# 初始化情绪分析服务uv
emotion_service = EmotionAnalyticsService()



@router.post("/tags", response_model=ApiResponse)
async def get_emotion_tags(
    request: EmotionTagsRequest,
    current_user: User = Depends(get_current_user),
):

    try:
        api_logger.info(
            f"用户 {current_user.username} 请求获取情绪标签统计",
            extra={
                "group_id": request.group_id,
                "emotion_type": request.emotion_type,
                "start_date": request.start_date,
                "end_date": request.end_date,
                "limit": request.limit
            }
        )
        
        # 调用服务层
        data = await emotion_service.get_emotion_tags(
            end_user_id=request.group_id,
            emotion_type=request.emotion_type,
            start_date=request.start_date,
            end_date=request.end_date,
            limit=request.limit
        )
        
        api_logger.info(
            "情绪标签统计获取成功",
            extra={
                "group_id": request.group_id,
                "total_count": data.get("total_count", 0),
                "tags_count": len(data.get("tags", []))
            }
        )
        
        return success(data=data, msg="情绪标签获取成功")
        
    except Exception as e:
        api_logger.error(
            f"获取情绪标签统计失败: {str(e)}",
            extra={"group_id": request.group_id},
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取情绪标签统计失败: {str(e)}"
        )



@router.post("/wordcloud", response_model=ApiResponse)
async def get_emotion_wordcloud(
    request: EmotionWordcloudRequest,
    current_user: User = Depends(get_current_user),
):

    try:
        api_logger.info(
            f"用户 {current_user.username} 请求获取情绪词云数据",
            extra={
                "group_id": request.group_id,
                "emotion_type": request.emotion_type,
                "limit": request.limit
            }
        )
        
        # 调用服务层
        data = await emotion_service.get_emotion_wordcloud(
            end_user_id=request.group_id,
            emotion_type=request.emotion_type,
            limit=request.limit
        )
        
        api_logger.info(
            "情绪词云数据获取成功",
            extra={
                "group_id": request.group_id,
                "total_keywords": data.get("total_keywords", 0)
            }
        )
        
        return success(data=data, msg="情绪词云获取成功")
        
    except Exception as e:
        api_logger.error(
            f"获取情绪词云数据失败: {str(e)}",
            extra={"group_id": request.group_id},
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取情绪词云数据失败: {str(e)}"
        )



@router.post("/health", response_model=ApiResponse)
async def get_emotion_health(
    request: EmotionHealthRequest,
    current_user: User = Depends(get_current_user),
):

    try:
        # 验证时间范围参数
        if request.time_range not in ["7d", "30d", "90d"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="时间范围参数无效，必须是 7d、30d 或 90d"
            )
        
        api_logger.info(
            f"用户 {current_user.username} 请求获取情绪健康指数",
            extra={
                "group_id": request.group_id,
                "time_range": request.time_range
            }
        )
        
        # 调用服务层
        data = await emotion_service.calculate_emotion_health_index(
            end_user_id=request.group_id,
            time_range=request.time_range
        )
        
        api_logger.info(
            "情绪健康指数获取成功",
            extra={
                "group_id": request.group_id,
                "health_score": data.get("health_score", 0),
                "level": data.get("level", "未知")
            }
        )
        
        return success(data=data, msg="情绪健康指数获取成功")
        
    except HTTPException:
        raise
    except Exception as e:
        api_logger.error(
            f"获取情绪健康指数失败: {str(e)}",
            extra={"group_id": request.group_id},
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取情绪健康指数失败: {str(e)}"
        )



@router.post("/suggestions", response_model=ApiResponse)
async def get_emotion_suggestions(
    request: EmotionSuggestionsRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取个性化情绪建议
    
    Args:
        request: 包含 group_id 和可选的 config_id
        db: 数据库会话
        current_user: 当前用户
        
    Returns:
        个性化情绪建议响应
    """
    try:
        # 验证 config_id（如果提供）
        config_id = request.config_id
        if config_id is not None:
            from app.controllers.memory_agent_controller import validate_config_id
            try:
                config_id = validate_config_id(config_id, db)
            except ValueError as e:
                return fail(BizCode.INVALID_PARAMETER, "配置ID无效", str(e))
        
        api_logger.info(
            f"用户 {current_user.username} 请求获取个性化情绪建议",
            extra={
                "group_id": request.group_id,
                "config_id": config_id
            }
        )
        
        # 调用服务层
        data = await emotion_service.generate_emotion_suggestions(
            end_user_id=request.group_id,
            config_id=config_id
        )
        
        api_logger.info(
            "个性化建议获取成功",
            extra={
                "group_id": request.group_id,
                "suggestions_count": len(data.get("suggestions", []))
            }
        )
        
        return success(data=data, msg="个性化建议获取成功")
        
    except Exception as e:
        api_logger.error(
            f"获取个性化建议失败: {str(e)}",
            extra={"group_id": request.group_id},
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取个性化建议失败: {str(e)}"
        )
