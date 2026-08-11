# -*- coding: utf-8 -*-
"""情绪配置服务模块

本模块提供情绪引擎配置的管理功能，包括获取和更新配置。

Classes:
    EmotionConfigService: 情绪配置服务，提供配置管理功能
"""

from typing import Dict, Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.memory_config_model import MemoryConfig
from app.models.workspace_model import Workspace
from app.core.logging_config import get_business_logger

logger = get_business_logger()


class EmotionConfigService:
    """情绪配置服务
    
    提供情绪引擎配置的管理功能，包括：
    - 获取情绪配置
    - 更新情绪配置
    - 验证配置参数
    
    Attributes:
        db: 数据库会话
    """
    
    def __init__(self, db: AsyncSession):
        """初始化情绪配置服务
        
        Args:
            db: 数据库会话
        """
        self.db = db
        logger.info("情绪配置服务初始化完成")
    
    def validate_emotion_config(self, config_data: Dict[str, Any]) -> bool:
        """验证情绪配置参数
        
        验证配置参数的有效性，包括：
        - emotion_min_intensity 在 [0.0, 1.0] 范围内
        - 布尔字段类型正确
        
        Args:
            config_data: 配置数据字典
            
        Returns:
            bool: 验证是否通过
            
        Raises:
            ValueError: 当配置参数无效时
        """
        try:
            logger.debug(f"验证情绪配置参数: {config_data}")
            
            # 验证 emotion_min_intensity 范围
            if "emotion_min_intensity" in config_data:
                min_intensity = config_data["emotion_min_intensity"]
                if not isinstance(min_intensity, (int, float)):
                    raise ValueError("emotion_min_intensity 必须是数字类型")
                if not (0.0 <= min_intensity <= 1.0):
                    raise ValueError("emotion_min_intensity 必须在 0.0 到 1.0 之间")
            
            # 验证布尔字段
            bool_fields = ["emotion_enabled", "emotion_extract_keywords", "emotion_enable_subject"]
            for field in bool_fields:
                if field in config_data:
                    value = config_data[field]
                    if not isinstance(value, bool):
                        raise ValueError(f"{field} 必须是布尔类型")
            
            logger.debug("情绪配置参数验证通过")
            return True
            
        except ValueError as e:
            logger.warning(f"配置参数验证失败: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"验证配置参数时发生错误: {str(e)}", exc_info=True)
            raise ValueError(f"验证配置参数失败: {str(e)}")
    
    async def get_emotion_config_async(self, config_id: UUID) -> Dict[str, Any]:
        """获取情绪引擎配置（异步版本）

        使用 select() + await self.db.execute() 模式替代 self.db.query()。

        Args:
            config_id: 配置ID

        Returns:
            Dict: 包含情绪配置的响应数据

        Raises:
            ValueError: 当配置不存在时
        """
        try:
            logger.info(f"获取情绪配置（异步）: config_id={config_id}")

            stmt = select(MemoryConfig, Workspace).join(
                Workspace, MemoryConfig.workspace_id == Workspace.id
            ).where(MemoryConfig.config_id == config_id)
            result = await self.db.execute(stmt)
            row = result.first()
            if not row:
                logger.error(f"配置不存在: config_id={config_id}")
                raise ValueError(f"配置不存在: config_id={config_id}")
            config, workspace = row

            emotion_config = {
                "config_id": config.config_id,
                "emotion_enabled": config.emotion_enabled,
                "emotion_model_id": workspace.llm,
                "emotion_extract_keywords": config.emotion_extract_keywords,
                "emotion_min_intensity": config.emotion_min_intensity,
                "emotion_enable_subject": config.emotion_enable_subject,
                "is_default": bool(config.is_default)
            }

            logger.info(f"情绪配置获取成功（异步）: config_id={config_id}")
            return emotion_config

        except ValueError:
            raise
        except Exception as e:
            logger.error(f"获取情绪配置失败（异步）: {str(e)}", exc_info=True)
            raise

    async def update_emotion_config_async(
        self,
        config_id: UUID,
        config_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """更新情绪引擎配置（异步版本）

        使用 select() + await self.db.execute() 模式替代 self.db.query()，
        commit/refresh/rollback 前加 await。

        Args:
            config_id: 配置ID
            config_data: 要更新的配置数据

        Returns:
            Dict: 更新后的完整情绪配置

        Raises:
            ValueError: 当配置不存在或参数无效时
        """
        try:
            logger.info(f"更新情绪配置（异步）: config_id={config_id}, data={config_data}")

            # 验证配置参数
            self.validate_emotion_config(config_data)

            stmt = select(MemoryConfig).where(MemoryConfig.config_id == config_id)
            result = await self.db.execute(stmt)
            config = result.scalars().first()

            if not config:
                logger.error(f"配置不存在: config_id={config_id}")
                raise ValueError(f"配置不存在: config_id={config_id}")

            # 更新字段
            if "emotion_enabled" in config_data:
                config.emotion_enabled = config_data["emotion_enabled"]
            if "emotion_extract_keywords" in config_data:
                config.emotion_extract_keywords = config_data["emotion_extract_keywords"]
            if "emotion_min_intensity" in config_data:
                config.emotion_min_intensity = config_data["emotion_min_intensity"]
            if "emotion_enable_subject" in config_data:
                config.emotion_enable_subject = config_data["emotion_enable_subject"]

            await self.db.commit()
            await self.db.refresh(config)

            # 返回更新后的配置
            updated_config = await self.get_emotion_config_async(config_id)

            logger.info(f"情绪配置更新成功（异步）: config_id={config_id}")
            return updated_config

        except ValueError:
            await self.db.rollback()
            raise
        except Exception as e:
            await self.db.rollback()
            logger.error(f"更新情绪配置失败（异步）: {str(e)}", exc_info=True)
            raise
