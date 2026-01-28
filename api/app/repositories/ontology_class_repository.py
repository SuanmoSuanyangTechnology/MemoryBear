# -*- coding: utf-8 -*-
"""本体类型Repository层

本模块提供本体类型的数据访问层实现。

Classes:
    OntologyClassRepository: 本体类型数据访问类
"""

import logging
from typing import List, Optional
from uuid import UUID

from sqlalchemy.orm import Session, joinedload

from app.core.logging_config import get_db_logger
from app.models.ontology_class import OntologyClass
from app.models.ontology_scene import OntologyScene


logger = get_db_logger()


class OntologyClassRepository:
    """本体类型Repository
    
    提供本体类型的CRUD操作和权限检查。
    
    Attributes:
        db: SQLAlchemy数据库会话
    """
    
    def __init__(self, db: Session):
        """初始化Repository
        
        Args:
            db: SQLAlchemy数据库会话
        """
        self.db = db
    
    def create(self, class_data: dict, scene_id: UUID) -> OntologyClass:
        """创建本体类型
        
        Args:
            class_data: 类型数据字典，包含class_name和class_description
            scene_id: 所属场景ID
            
        Returns:
            OntologyClass: 创建的类型对象
            
        Raises:
            Exception: 数据库操作失败
            
        Examples:
            >>> repo = OntologyClassRepository(db)
            >>> ontology_class = repo.create(
            ...     {"class_name": "患者", "class_description": "描述"},
            ...     scene_id
            ... )
        """
        try:
            logger.info(
                f"Creating ontology class - "
                f"name={class_data.get('class_name')}, "
                f"scene_id={scene_id}"
            )
            
            ontology_class = OntologyClass(
                class_name=class_data.get("class_name"),
                class_description=class_data.get("class_description"),
                scene_id=scene_id
            )
            
            self.db.add(ontology_class)
            self.db.flush()  # 获取ID但不提交
            
            logger.info(
                f"Ontology class created successfully - "
                f"class_id={ontology_class.class_id}"
            )
            
            return ontology_class
            
        except Exception as e:
            logger.error(
                f"Failed to create ontology class: {str(e)}",
                exc_info=True
            )
            raise
    
    def get_by_id(self, class_id: UUID) -> Optional[OntologyClass]:
        """根据ID获取类型
        
        Args:
            class_id: 类型ID
            
        Returns:
            Optional[OntologyClass]: 类型对象，不存在则返回None
            
        Examples:
            >>> repo = OntologyClassRepository(db)
            >>> ontology_class = repo.get_by_id(class_id)
        """
        try:
            logger.debug(f"Getting ontology class by ID: {class_id}")
            
            ontology_class = self.db.query(OntologyClass).filter(
                OntologyClass.class_id == class_id
            ).first()
            
            if ontology_class:
                logger.debug(f"Ontology class found: {class_id}")
            else:
                logger.debug(f"Ontology class not found: {class_id}")
            
            return ontology_class
            
        except Exception as e:
            logger.error(
                f"Failed to get ontology class by ID: {str(e)}",
                exc_info=True
            )
            raise
    
    def get_by_name(self, class_name: str, scene_id: UUID) -> Optional[OntologyClass]:
        """根据类型名称和场景ID获取类型
        
        Args:
            class_name: 类型名称
            scene_id: 场景ID
            
        Returns:
            Optional[OntologyClass]: 类型对象，不存在则返回None
            
        Examples:
            >>> repo = OntologyClassRepository(db)
            >>> ontology_class = repo.get_by_name("患者", scene_id)
        """
        try:
            logger.debug(f"Getting ontology class by name: {class_name}, scene_id: {scene_id}")
            
            ontology_class = self.db.query(OntologyClass).filter(
                OntologyClass.class_name == class_name,
                OntologyClass.scene_id == scene_id
            ).first()
            
            if ontology_class:
                logger.debug(f"Ontology class found: {class_name}")
            else:
                logger.debug(f"Ontology class not found: {class_name}")
            
            return ontology_class
            
        except Exception as e:
            logger.error(
                f"Failed to get ontology class by name: {str(e)}",
                exc_info=True
            )
            raise
    
    def get_by_scene(self, scene_id: UUID) -> List[OntologyClass]:
        """获取场景下的所有类型
        
        按创建时间倒序排列。
        
        Args:
            scene_id: 场景ID
            
        Returns:
            List[OntologyClass]: 类型列表
            
        Examples:
            >>> repo = OntologyClassRepository(db)
            >>> classes = repo.get_by_scene(scene_id)
        """
        try:
            logger.debug(f"Getting ontology classes by scene: {scene_id}")
            
            classes = self.db.query(OntologyClass).filter(
                OntologyClass.scene_id == scene_id
            ).order_by(
                OntologyClass.created_at.desc()
            ).all()
            
            logger.info(
                f"Found {len(classes)} ontology classes in scene {scene_id}"
            )
            
            return classes
            
        except Exception as e:
            logger.error(
                f"Failed to get ontology classes by scene: {str(e)}",
                exc_info=True
            )
            raise
    
    def update(self, class_id: UUID, update_data: dict) -> Optional[OntologyClass]:
        """更新类型信息
        
        Args:
            class_id: 类型ID
            update_data: 更新数据字典
            
        Returns:
            Optional[OntologyClass]: 更新后的类型对象，不存在则返回None
            
        Raises:
            Exception: 数据库操作失败
            
        Examples:
            >>> repo = OntologyClassRepository(db)
            >>> ontology_class = repo.update(
            ...     class_id,
            ...     {"class_name": "新名称"}
            ... )
        """
        try:
            logger.info(f"Updating ontology class: {class_id}")
            
            ontology_class = self.get_by_id(class_id)
            if not ontology_class:
                logger.warning(f"Ontology class not found for update: {class_id}")
                return None
            
            # 更新字段
            if "class_name" in update_data and update_data["class_name"] is not None:
                ontology_class.class_name = update_data["class_name"]
            
            if "class_description" in update_data:
                ontology_class.class_description = update_data["class_description"]
            
            self.db.flush()
            
            logger.info(f"Ontology class updated successfully: {class_id}")
            
            return ontology_class
            
        except Exception as e:
            logger.error(
                f"Failed to update ontology class: {str(e)}",
                exc_info=True
            )
            raise
    
    def delete(self, class_id: UUID) -> bool:
        """删除类型
        
        Args:
            class_id: 类型ID
            
        Returns:
            bool: 删除成功返回True，类型不存在返回False
            
        Raises:
            Exception: 数据库操作失败
            
        Examples:
            >>> repo = OntologyClassRepository(db)
            >>> success = repo.delete(class_id)
        """
        try:
            logger.info(f"Deleting ontology class: {class_id}")
            
            ontology_class = self.get_by_id(class_id)
            if not ontology_class:
                logger.warning(f"Ontology class not found for delete: {class_id}")
                return False
            
            self.db.delete(ontology_class)
            self.db.flush()
            
            logger.info(f"Ontology class deleted successfully: {class_id}")
            
            return True
            
        except Exception as e:
            logger.error(
                f"Failed to delete ontology class: {str(e)}",
                exc_info=True
            )
            raise
    
    def check_ownership(self, class_id: UUID, workspace_id: UUID) -> bool:
        """检查类型是否属于指定工作空间（通过场景关联）
        
        Args:
            class_id: 类型ID
            workspace_id: 工作空间ID
            
        Returns:
            bool: 属于返回True，否则返回False
            
        Examples:
            >>> repo = OntologyClassRepository(db)
            >>> is_owner = repo.check_ownership(class_id, workspace_id)
        """
        try:
            logger.debug(
                f"Checking class ownership - "
                f"class_id={class_id}, workspace_id={workspace_id}"
            )
            
            count = self.db.query(OntologyClass).join(
                OntologyScene,
                OntologyClass.scene_id == OntologyScene.scene_id
            ).filter(
                OntologyClass.class_id == class_id,
                OntologyScene.workspace_id == workspace_id
            ).count()
            
            is_owner = count > 0
            
            logger.debug(
                f"Class ownership check result: {is_owner} - "
                f"class_id={class_id}"
            )
            
            return is_owner
            
        except Exception as e:
            logger.error(
                f"Failed to check class ownership: {str(e)}",
                exc_info=True
            )
            raise
    
    def get_scene_id_by_class(self, class_id: UUID) -> Optional[UUID]:
        """根据类型ID获取所属场景ID
        
        Args:
            class_id: 类型ID
            
        Returns:
            Optional[UUID]: 场景ID，类型不存在则返回None
            
        Examples:
            >>> repo = OntologyClassRepository(db)
            >>> scene_id = repo.get_scene_id_by_class(class_id)
        """
        try:
            logger.debug(f"Getting scene ID by class: {class_id}")
            
            ontology_class = self.get_by_id(class_id)
            if not ontology_class:
                logger.debug(f"Class not found: {class_id}")
                return None
            
            logger.debug(
                f"Found scene ID: {ontology_class.scene_id} for class: {class_id}"
            )
            
            return ontology_class.scene_id
            
        except Exception as e:
            logger.error(
                f"Failed to get scene ID by class: {str(e)}",
                exc_info=True
            )
            raise
