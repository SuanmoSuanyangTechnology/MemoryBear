# -*- coding: utf-8 -*-
"""情绪配置服务模块

本模块提供情绪引擎配置的管理功能，包括获取和更新配置。

Classes:
    EmotionConfigService: 情绪配置服务，提供配置管理功能
"""

from typing import Dict, Any
from sqlalchemy.orm import Session

from app.models.data_config_model import DataConfig
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
    
    def __init__(self, db: Session):
        """初始化情绪配置服务
        
        Args:
            db: 数据库会话
        """
        self.db = db
        logger.info("情绪配置服务初始化完成")
    
    def get_emotion_config(self, config_id: int) -> Dict[str, Any]:
        """获取情绪引擎配置
        
        查询指定配置ID的情绪相关配置字段。
        
        Args:
            config_id: 配置ID
            
        Returns:
            Dict: 包含情绪配置的响应数据：
                - config_id: 配置ID
                - emotion_enabled: 是否启用情绪提取
                - emotion_model_id: 情绪分析专用模型ID
                - emotion_extract_keywords: 是否提取情绪关键词
                - emotion_min_intensity: 最小情绪强度阈值
                - emotion_enable_subject: 是否启用主体分类
                
        Raises:
            ValueError: 当配置不存在时
        """
        try:
            logger.info(f"获取情绪配置: config_id={config_id}")
            
            # 查询配置
            config = self.db.query(DataConfig).filter(
                DataConfig.config_id == config_id
            ).first()
            
            if not config:
                logger.error(f"配置不存在: config_id={config_id}")
                raise ValueError(f"配置不存在: config_id={config_id}")
            
            # 提取情绪相关字段
            emotion_config = {
                "config_id": config.config_id,
                "emotion_enabled": config.emotion_enabled,
                "emotion_model_id": config.emotion_model_id,
                "emotion_extract_keywords": config.emotion_extract_keywords,
                "emotion_min_intensity": config.emotion_min_intensity,
                "emotion_enable_subject": config.emotion_enable_subject
            }
            
            logger.info(f"情绪配置获取成功: config_id={config_id}")
            return emotion_config
            
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"获取情绪配置失败: {str(e)}", exc_info=True)
            raise
    
    def validate_emotion_config(self, config_data: Dict[str, Any]) -> bool:
        """验证情绪配置参数
        
        验证配置参数的有效性，包括：
        - emotion_min_intensity 在 [0.0, 1.0] 范围内
        - 布尔字段类型正确
        - emotion_model_id 格式有效（如果提供）
        
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
            
            # 验证 emotion_model_id（如果提供）
            if "emotion_model_id" in config_data:
                model_id = config_data["emotion_model_id"]
                if model_id is not None and not isinstance(model_id, str):
                    raise ValueError("emotion_model_id 必须是字符串类型或 null")
                if model_id is not None and len(model_id.strip()) == 0:
                    raise ValueError("emotion_model_id 不能为空字符串")
            
            logger.debug("情绪配置参数验证通过")
            return True
            
        except ValueError as e:
            logger.warning(f"配置参数验证失败: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"验证配置参数时发生错误: {str(e)}", exc_info=True)
            raise ValueError(f"验证配置参数失败: {str(e)}")
    
    def update_emotion_config(
        self,
        config_id: int,
        config_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """更新情绪引擎配置
        
        更新指定配置ID的情绪相关配置字段。
        
        Args:
            config_id: 配置ID
            config_data: 要更新的配置数据，可包含以下字段：
                - emotion_enabled: 是否启用情绪提取
                - emotion_model_id: 情绪分析专用模型ID
                - emotion_extract_keywords: 是否提取情绪关键词
                - emotion_min_intensity: 最小情绪强度阈值
                - emotion_enable_subject: 是否启用主体分类
                
        Returns:
            Dict: 更新后的完整情绪配置
            
        Raises:
            ValueError: 当配置不存在或参数无效时
        """
        try:
            logger.info(f"更新情绪配置: config_id={config_id}, data={config_data}")
            
            # 验证配置参数
            self.validate_emotion_config(config_data)
            
            # 查询配置
            config = self.db.query(DataConfig).filter(
                DataConfig.config_id == config_id
            ).first()
            
            if not config:
                logger.error(f"配置不存在: config_id={config_id}")
                raise ValueError(f"配置不存在: config_id={config_id}")
            
            # 更新字段
            if "emotion_enabled" in config_data:
                config.emotion_enabled = config_data["emotion_enabled"]
            if "emotion_model_id" in config_data:
                config.emotion_model_id = config_data["emotion_model_id"]
            if "emotion_extract_keywords" in config_data:
                config.emotion_extract_keywords = config_data["emotion_extract_keywords"]
            if "emotion_min_intensity" in config_data:
                config.emotion_min_intensity = config_data["emotion_min_intensity"]
            if "emotion_enable_subject" in config_data:
                config.emotion_enable_subject = config_data["emotion_enable_subject"]
            
            # 提交更改
            self.db.commit()
            self.db.refresh(config)
            
            # 返回更新后的配置
            updated_config = self.get_emotion_config(config_id)
            
            logger.info(f"情绪配置更新成功: config_id={config_id}")
            return updated_config
            
        except ValueError:
            self.db.rollback()
            raise
        except Exception as e:
            self.db.rollback()
            logger.error(f"更新情绪配置失败: {str(e)}", exc_info=True)
            raise
