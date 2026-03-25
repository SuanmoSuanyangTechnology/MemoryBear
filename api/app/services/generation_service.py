"""
图片和视频生成服务

提供统一的生成接口，支持多种 Provider
"""
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session
import uuid

from app.core.models import RedBearModelConfig, RedBearImageGenerator, RedBearVideoGenerator
from app.core.exceptions import BusinessException
from app.core.error_codes import BizCode
from app.models.models_model import ModelType
from app.repositories.model_repository import ModelConfigRepository, ModelApiKeyRepository
from app.services.model_service import ModelApiKeyService


class GenerationService:
    """生成服务"""
    
    def __init__(self, db: Session):
        self.db = db
    
    async def generate_image(
        self,
        model_config_id: str,
        prompt: str,
        size: Optional[str] = "1024x1024",
        n: int = 1,
        **kwargs
    ) -> Dict[str, Any]:
        """
        生成图片
        
        Args:
            model_config_id: 模型配置ID
            prompt: 提示词
            size: 图片尺寸
            n: 生成数量
            **kwargs: 其他参数
            
        Returns:
            生成结果
        """
        # 获取模型配置
        model_config = ModelConfigRepository.get_by_id(self.db, uuid.UUID(model_config_id))
        if not model_config:
            raise BusinessException("模型配置不存在", code=BizCode.NOT_FOUND)
        
        if model_config.type != ModelType.IMAGE:
            raise BusinessException(
                f"模型类型错误，期望 {ModelType.IMAGE}，实际 {model_config.type}",
                code=BizCode.INVALID_PARAMETER
            )
        
        # 获取 API Key
        api_key_info = ModelApiKeyService.get_available_api_key(self.db, uuid.UUID(model_config_id))
        if not api_key_info:
            raise BusinessException("没有可用的 API Key", code=BizCode.NOT_FOUND)
        
        # 创建配置
        config = RedBearModelConfig(
            model_name=api_key_info.model_name,
            provider=api_key_info.provider,
            api_key=api_key_info.api_key,
            base_url=api_key_info.api_base,
            extra_params=api_key_info.config or {}
        )
        
        # 生成图片
        generator = RedBearImageGenerator(config)
        result = await generator.agenerate(prompt, size, n, **kwargs)
        
        return result
    
    async def generate_video(
        self,
        model_config_id: str,
        prompt: str,
        duration: Optional[int] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        生成视频
        
        Args:
            model_config_id: 模型配置ID
            prompt: 提示词
            duration: 视频时长（秒）
            **kwargs: 其他参数
            
        Returns:
            生成结果（包含任务ID）
        """
        # 获取模型配置
        model_config = ModelConfigRepository.get_by_id(self.db, uuid.UUID(model_config_id))
        if not model_config:
            raise BusinessException("模型配置不存在", code=BizCode.NOT_FOUND)
        
        if model_config.type != ModelType.VIDEO:
            raise BusinessException(
                f"模型类型错误，期望 {ModelType.VIDEO}，实际 {model_config.type}",
                code=BizCode.INVALID_PARAMETER
            )
        
        # 获取 API Key
        api_key_info = ModelApiKeyService.get_available_api_key(self.db, uuid.UUID(model_config_id))
        if not api_key_info:
            raise BusinessException("没有可用的 API Key", code=BizCode.NOT_FOUND)
        
        # 创建配置
        config = RedBearModelConfig(
            model_name=api_key_info.model_name,
            provider=api_key_info.provider,
            api_key=api_key_info.api_key,
            base_url=api_key_info.api_base,
            extra_params=api_key_info.config or {}
        )
        
        # 生成视频
        generator = RedBearVideoGenerator(config)
        result = await generator.agenerate(prompt, duration, **kwargs)
        
        return result
    
    async def get_video_task_status(
        self,
        model_config_id: str,
        task_id: str
    ) -> Dict[str, Any]:
        """
        查询视频生成任务状态
        
        Args:
            model_config_id: 模型配置ID
            task_id: 任务ID
            
        Returns:
            任务状态信息
        """
        # 获取模型配置
        model_config = ModelConfigRepository.get_by_id(self.db, uuid.UUID(model_config_id))
        if not model_config:
            raise BusinessException("模型配置不存在", code=BizCode.NOT_FOUND)
        
        # 获取 API Key
        api_key_info = ModelApiKeyService.get_available_api_key(self.db, uuid.UUID(model_config_id))
        if not api_key_info:
            raise BusinessException("没有可用的 API Key", code=BizCode.NOT_FOUND)
        
        # 创建配置
        config = RedBearModelConfig(
            model_name=api_key_info.model_name,
            provider=api_key_info.provider,
            api_key=api_key_info.api_key,
            base_url=api_key_info.api_base,
            extra_params=api_key_info.config or {}
        )
        
        # 查询任务状态
        generator = RedBearVideoGenerator(config)
        result = await generator.aget_task_status(task_id)
        
        return result
