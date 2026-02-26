# -*- coding: utf-8 -*-
"""默认本体场景初始化器

本模块提供默认本体场景和类型的自动初始化功能。
在工作空间创建时，自动添加预设的本体场景和实体类型。

Classes:
    DefaultOntologyInitializer: 默认本体场景初始化器
"""

import logging
from typing import List, Optional, Tuple
from uuid import UUID

from sqlalchemy.orm import Session

from app.config.default_ontology_config import (
    DEFAULT_SCENES,
    get_scene_name,
    get_scene_description,
    get_type_name,
    get_type_description,
)
from app.core.logging_config import get_business_logger
from app.repositories.ontology_scene_repository import OntologySceneRepository
from app.repositories.ontology_class_repository import OntologyClassRepository


class DefaultOntologyInitializer:
    """默认本体场景初始化器
    
    负责在工作空间创建时自动初始化默认的本体场景和类型。
    遵循最小侵入原则，确保初始化失败不阻止工作空间创建。
    
    Attributes:
        db: 数据库会话
        scene_repo: 场景Repository
        class_repo: 类型Repository
        logger: 业务日志记录器
    """
    
    def __init__(self, db: Session):
        """初始化
        
        Args:
            db: 数据库会话
        """
        self.db = db
        self.scene_repo = OntologySceneRepository(db)
        self.class_repo = OntologyClassRepository(db)
        self.logger = get_business_logger()
    
    def initialize_default_scenes(
        self,
        workspace_id: UUID,
        language: str = "zh"
    ) -> Tuple[bool, str]:
        """为工作空间初始化默认场景
        
        创建两个默认场景（在线教育、情感陪伴）及其对应的实体类型。
        如果创建失败，记录错误日志但不抛出异常。
        
        Args:
            workspace_id: 工作空间ID
            language: 语言类型 ("zh" 或 "en")，默认为 "zh"
            
        Returns:
            Tuple[bool, str]: (是否成功, 错误信息)
        """
        try:
            self.logger.info(
                f"开始初始化默认本体场景 - workspace_id={workspace_id}, language={language}"
            )
            
            scenes_created = 0
            total_types_created = 0
            
            # 遍历默认场景配置
            for scene_config in DEFAULT_SCENES:
                scene_name = get_scene_name(scene_config, language)
                
                # 创建场景及其类型
                scene_id = self._create_scene_with_types(workspace_id, scene_config, language)
                
                if scene_id:
                    scenes_created += 1
                    # 统计类型数量
                    types_count = len(scene_config.get("types", []))
                    total_types_created += types_count
                    
                    self.logger.info(
                        f"场景创建成功 - scene_name={scene_name}, "
                        f"scene_id={scene_id}, types_count={types_count}, language={language}"
                    )
                else:
                    self.logger.warning(
                        f"场景创建失败 - scene_name={scene_name}, "
                        f"workspace_id={workspace_id}, language={language}"
                    )
            
            # 记录总体结果
            self.logger.info(
                f"默认场景初始化完成 - workspace_id={workspace_id}, "
                f"language={language}, scenes_created={scenes_created}, "
                f"total_types_created={total_types_created}"
            )
            
            # 如果至少创建了一个场景，视为成功
            if scenes_created > 0:
                return True, ""
            else:
                error_msg = "所有默认场景创建失败"
                self.logger.error(
                    f"默认场景初始化失败 - workspace_id={workspace_id}, "
                    f"language={language}, error={error_msg}"
                )
                return False, error_msg
                
        except Exception as e:
            error_msg = f"默认场景初始化异常: {str(e)}"
            self.logger.error(
                f"默认场景初始化异常 - workspace_id={workspace_id}, "
                f"language={language}, error={str(e)}",
                exc_info=True
            )
            return False, error_msg
    
    def _create_scene_with_types(
        self,
        workspace_id: UUID,
        scene_config: dict,
        language: str = "zh"
    ) -> Optional[UUID]:
        """创建场景及其类型
        
        Args:
            workspace_id: 工作空间ID
            scene_config: 场景配置字典
            language: 语言类型 ("zh" 或 "en")
            
        Returns:
            Optional[UUID]: 创建的场景ID，失败返回None
        """
        try:
            scene_name = get_scene_name(scene_config, language)
            scene_description = get_scene_description(scene_config, language)
            
            # 检查是否已存在同名场景（支持向后兼容）
            existing_scene = self.scene_repo.get_by_name(scene_name, workspace_id)
            if existing_scene:
                self.logger.info(
                    f"场景已存在，跳过创建 - scene_name={scene_name}, "
                    f"workspace_id={workspace_id}, scene_id={existing_scene.scene_id}, "
                    f"language={language}"
                )
                return None
            
            # 创建场景记录，设置 is_system_default=true
            scene_data = {
                "scene_name": scene_name,
                "scene_description": scene_description
            }
            
            scene = self.scene_repo.create(scene_data, workspace_id)
            
            # 设置系统默认标识
            scene.is_system_default = True
            self.db.flush()
            
            self.logger.info(
                f"场景创建成功 - scene_name={scene_name}, "
                f"scene_id={scene.scene_id}, is_system_default=True, language={language}"
            )
            
            # 批量创建类型
            types_config = scene_config.get("types", [])
            types_created = self._batch_create_types(scene.scene_id, types_config, language)
            
            self.logger.info(
                f"场景类型创建完成 - scene_id={scene.scene_id}, "
                f"types_created={types_created}/{len(types_config)}, language={language}"
            )
            
            return scene.scene_id
            
        except Exception as e:
            scene_name = get_scene_name(scene_config, language)
            self.logger.error(
                f"场景创建失败 - scene_name={scene_name}, "
                f"workspace_id={workspace_id}, language={language}, error={str(e)}",
                exc_info=True
            )
            return None
    
    def _batch_create_types(
        self,
        scene_id: UUID,
        types_config: List[dict],
        language: str = "zh"
    ) -> int:
        """批量创建实体类型
        
        Args:
            scene_id: 场景ID
            types_config: 类型配置列表
            language: 语言类型 ("zh" 或 "en")
            
        Returns:
            int: 成功创建的类型数量
        """
        created_count = 0
        
        for type_config in types_config:
            try:
                type_name = get_type_name(type_config, language)
                type_description = get_type_description(type_config, language)
                
                # 创建类型数据
                class_data = {
                    "class_name": type_name,
                    "class_description": type_description
                }
                
                # 创建类型
                ontology_class = self.class_repo.create(class_data, scene_id)
                
                # 设置系统默认标识
                ontology_class.is_system_default = True
                self.db.flush()
                
                created_count += 1
                
                self.logger.debug(
                    f"类型创建成功 - class_name={type_name}, "
                    f"class_id={ontology_class.class_id}, "
                    f"scene_id={scene_id}, is_system_default=True, language={language}"
                )
                
            except Exception as e:
                type_name = get_type_name(type_config, language)
                self.logger.warning(
                    f"单个类型创建失败，继续创建其他类型 - "
                    f"class_name={type_name}, scene_id={scene_id}, "
                    f"language={language}, error={str(e)}"
                )
                # 继续创建其他类型
                continue
        
        return created_count
