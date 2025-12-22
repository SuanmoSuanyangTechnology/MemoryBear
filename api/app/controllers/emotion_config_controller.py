# -*- coding: utf-8 -*-
"""情绪配置控制器模块

本模块提供情绪引擎配置管理的API端点，包括获取和更新配置。

Routes:
    GET /memory/config/emotion - 获取情绪引擎配置
    POST /memory/config/emotion - 更新情绪引擎配置
"""

from fastapi import APIRouter, Depends, Query, HTTPException, status
from pydantic import BaseModel, Field
from typing import Optional
from sqlalchemy.orm import Session

from app.core.response_utils import success
from app.dependencies import get_current_user
from app.models.user_model import User
from app.schemas.response_schema import ApiResponse
from app.services.emotion_config_service import EmotionConfigService
from app.core.logging_config import get_api_logger
from app.db import get_db

# 获取API专用日志器
api_logger = get_api_logger()

router = APIRouter(
    prefix="/memory/emotion",
    tags=["Emotion Config"],
    dependencies=[Depends(get_current_user)]  # 所有路由都需要认证
)

class EmotionConfigQuery(BaseModel):
    """情绪配置查询请求模型"""
    config_id: int = Field(..., description="配置ID")

class EmotionConfigUpdate(BaseModel):
    """情绪配置更新请求模型"""
    config_id: int = Field(..., description="配置ID")
    emotion_enabled: bool = Field(..., description="是否启用情绪提取")
    emotion_model_id: Optional[str] = Field(None, description="情绪分析专用模型ID")
    emotion_extract_keywords: bool = Field(..., description="是否提取情绪关键词")
    emotion_min_intensity: float = Field(..., ge=0.0, le=1.0, description="最小情绪强度阈值（0.0-1.0）")
    emotion_enable_subject: bool = Field(..., description="是否启用主体分类")

@router.get("/read_config", response_model=ApiResponse)
def get_emotion_config(
    config_id: int = Query(..., description="配置ID"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取情绪引擎配置
    
    查询指定配置ID的情绪相关配置字段。
    
    Args:
        config_id: 配置ID
        
    Returns:
        ApiResponse: 包含情绪配置数据
        
    Example Response:
        {
            "code": 2000,
            "msg": "情绪配置获取成功",
            "data": {
                "config_id": 17,
                "emotion_enabled": true,
                "emotion_model_id": "gpt-4",
                "emotion_extract_keywords": true,
                "emotion_min_intensity": 0.1,
                "emotion_enable_subject": true
            }
        }
    """
    try:
        api_logger.info(
            f"用户 {current_user.username} 请求获取情绪配置",
            extra={"config_id": config_id}
        )
        
        # 初始化服务
        config_service = EmotionConfigService(db)
        
        # 调用服务层
        data = config_service.get_emotion_config(config_id)
        
        api_logger.info(
            "情绪配置获取成功",
            extra={
                "config_id": config_id,
                "emotion_enabled": data.get("emotion_enabled", False)
            }
        )
        
        return success(data=data, msg="情绪配置获取成功")
        
    except ValueError as e:
        api_logger.warning(
            f"获取情绪配置失败: {str(e)}",
            extra={"config_id": config_id}
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except Exception as e:
        api_logger.error(
            f"获取情绪配置失败: {str(e)}",
            extra={"config_id": config_id},
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取情绪配置失败: {str(e)}"
        )



@router.post("/updated_config", response_model=ApiResponse)
def update_emotion_config(
    config: EmotionConfigUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """更新情绪引擎配置
    
    更新指定配置ID的情绪相关配置字段。
    
    Args:
        config: 配置更新数据（包含config_id）
        
    Returns:
        ApiResponse: 包含更新后的情绪配置数据
        
    Example Request:
        {
            "config_id": 2,
            "emotion_enabled": true,
            "emotion_model_id": "gpt-4",
            "emotion_extract_keywords": true,
            "emotion_min_intensity": 0.1,
            "emotion_enable_subject": true
        }
        
    Example Response:
        {
            "code": 2000,
            "msg": "情绪配置更新成功",
            "data": {
                "config_id": 17,
                "emotion_enabled": true,
                "emotion_model_id": "gpt-4",
                "emotion_extract_keywords": true,
                "emotion_min_intensity": 0.2,
                "emotion_enable_subject": true
            }
        }
    """
    try:
        api_logger.info(
            f"用户 {current_user.username} 请求更新情绪配置",
            extra={
                "config_id": config.config_id,
                "emotion_enabled": config.emotion_enabled,
                "emotion_min_intensity": config.emotion_min_intensity
            }
        )
        
        # 初始化服务
        config_service = EmotionConfigService(db)
        
        # 转换为字典（排除config_id，因为它作为参数传递）
        config_data = config.model_dump(exclude={'config_id'})
        
        # 调用服务层
        data = config_service.update_emotion_config(config.config_id, config_data)
        
        api_logger.info(
            "情绪配置更新成功",
            extra={
                "config_id": config.config_id,
                "emotion_enabled": data.get("emotion_enabled", False)
            }
        )
        
        return success(data=data, msg="情绪配置更新成功")
        
    except ValueError as e:
        api_logger.warning(
            f"更新情绪配置失败: {str(e)}",
            extra={"config_id": config.config_id}
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        api_logger.error(
            f"更新情绪配置失败: {str(e)}",
            extra={"config_id": config.config_id},
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"更新情绪配置失败: {str(e)}"
        )
