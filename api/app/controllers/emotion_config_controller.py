# -*- coding: utf-8 -*-
"""情绪配置控制器模块

本模块提供情绪引擎配置管理的API端点，包括获取和更新配置。

Routes:
    GET /memory/config/emotion - 获取情绪引擎配置
    POST /memory/config/emotion - 更新情绪引擎配置
"""
import uuid
from typing import Optional, Union
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.dependencies import get_current_user

router = APIRouter(
    prefix="/memory/emotion",
    tags=["Emotion Config"],
    dependencies=[Depends(get_current_user)]  # 所有路由都需要认证
)

class EmotionConfigQuery(BaseModel):
    """情绪配置查询请求模型"""
    config_id: UUID = Field(..., description="配置ID")

class EmotionConfigUpdate(BaseModel):
    """情绪配置更新请求模型"""
    config_id: Union[uuid.UUID, int, str]= Field(..., description="配置ID")
    emotion_enabled: bool = Field(..., description="是否启用情绪提取")
    emotion_extract_keywords: bool = Field(..., description="是否提取情绪关键词")
    emotion_min_intensity: float = Field(..., ge=0.0, le=1.0, description="最小情绪强度阈值（0.0-1.0）")
    emotion_enable_subject: bool = Field(..., description="是否启用主体分类")

# ==================== 记忆配置接口已迁移 ====================
# get_emotion_config / update_emotion_config 已迁移至 memory_config_controller
# （/memory_config/read_config_emotion、/memory_config/update_config_emotion）。
# 本文件保留 EmotionConfigUpdate / EmotionConfigQuery 模型供其复用。

