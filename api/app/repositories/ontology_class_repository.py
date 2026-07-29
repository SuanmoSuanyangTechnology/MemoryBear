# -*- coding: utf-8 -*-
"""本体类型Repository层

本模块提供本体类型的数据访问层实现。

Classes:
    OntologyClassRepository: 本体类型数据访问类
"""

from typing import List, Optional
from uuid import UUID

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

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
    
    def __init__(self, db: Session | AsyncSession):
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
    
    def get_classes_by_scene(self, scene_id: UUID) -> List[OntologyClass]:
        """获取场景下的所有类型
        
        按创建时间倒序排列。
        
        Args:
            scene_id: 场景ID
            
        Returns:
            List[OntologyClass]: 类型列表
            
        Examples:
            >>> repo = OntologyClassRepository(db)
            >>> classes = repo.get_classes_by_scene(scene_id)
        """
        try:
            logger.debug(f"Getting ontology classes by scene: {scene_id}")
            
            classes = self.db.query(OntologyClass).filter(
                OntologyClass.scene_id == scene_id
            ).order_by(
                OntologyClass.created_at.desc()
            ).all()
            
            logger.info(
                f"Found {len(classes)} ontology classes in scene_id: {scene_id}"
            )
            
            return classes

        except Exception as e:
            logger.error(
                f"Failed to get ontology classes by scene: {str(e)}",
                exc_info=True
            )
            raise

    async def get_classes_by_scene_async(self, scene_id: UUID) -> List[OntologyClass]:
        """Async version of get_classes_by_scene — uses select() for AsyncSession."""
        try:
            logger.debug(f"Getting ontology classes by scene (async): {scene_id}")

            stmt = (
                select(OntologyClass)
                .where(OntologyClass.scene_id == scene_id)
                .order_by(OntologyClass.created_at.desc())
            )
            result = await self.db.execute(stmt)
            classes = result.scalars().all()

            logger.info(
                f"Found {len(classes)} ontology classes in scene_id: {scene_id}"
            )
            return classes

        except Exception as e:
            logger.error(
                f"Failed to get ontology classes by scene (async): {str(e)}",
                exc_info=True,
            )
            raise

    async def create_async(self, class_data: dict, scene_id: UUID) -> OntologyClass:
        """Async version of create"""
        try:
            logger.info(
                f"Creating ontology class (async) - "
                f"name={class_data.get('class_name')}, "
                f"scene_id={scene_id}"
            )
            ontology_class = OntologyClass(
                class_name=class_data.get("class_name"),
                class_description=class_data.get("class_description"),
                scene_id=scene_id
            )
            self.db.add(ontology_class)
            await self.db.flush()
            logger.info(f"Ontology class created successfully (async): {ontology_class.class_id}")
            return ontology_class
        except Exception as e:
            logger.error(f"Failed to create ontology class (async): {str(e)}", exc_info=True)
            raise

    async def get_by_id_async(self, class_id: UUID) -> Optional[OntologyClass]:
        """Async version of get_by_id"""
        try:
            logger.debug(f"Getting ontology class by ID (async): {class_id}")
            stmt = select(OntologyClass).where(OntologyClass.class_id == class_id)
            result = await self.db.execute(stmt)
            ontology_class = result.scalar_one_or_none()
            if ontology_class:
                logger.debug(f"Ontology class found (async): {class_id}")
            else:
                logger.debug(f"Ontology class not found (async): {class_id}")
            return ontology_class
        except Exception as e:
            logger.error(f"Failed to get ontology class by ID (async): {str(e)}", exc_info=True)
            raise

    async def get_by_name_async(self, class_name: str, scene_id: UUID) -> Optional[OntologyClass]:
        """Async version of get_by_name"""
        try:
            logger.debug(f"Getting ontology class by name (async): {class_name}, scene_id: {scene_id}")
            stmt = select(OntologyClass).where(
                OntologyClass.class_name == class_name,
                OntologyClass.scene_id == scene_id
            )
            result = await self.db.execute(stmt)
            ontology_class = result.scalar_one_or_none()
            if ontology_class:
                logger.debug(f"Ontology class found (async): {class_name}")
            else:
                logger.debug(f"Ontology class not found (async): {class_name}")
            return ontology_class
        except Exception as e:
            logger.error(f"Failed to get ontology class by name (async): {str(e)}", exc_info=True)
            raise

    async def search_by_name_async(self, keyword: str, scene_id: UUID) -> List[OntologyClass]:
        """Async version of search_by_name"""
        try:
            logger.debug(
                f"Searching ontology classes by keyword (async) - "
                f"keyword={keyword}, scene_id={scene_id}"
            )
            stmt = (
                select(OntologyClass)
                .where(
                    OntologyClass.class_name.ilike(f"%{keyword}%"),
                    OntologyClass.scene_id == scene_id
                )
                .order_by(OntologyClass.created_at.desc())
            )
            result = await self.db.execute(stmt)
            classes = result.scalars().all()
            logger.info(
                f"Found {len(classes)} ontology classes matching keyword '{keyword}' "
                f"in scene {scene_id} (async)"
            )
            return list(classes)
        except Exception as e:
            logger.error(f"Failed to search ontology classes by keyword (async): {str(e)}", exc_info=True)
            raise

    async def update_async(self, class_id: UUID, update_data: dict) -> Optional[OntologyClass]:
        """Async version of update"""
        try:
            logger.info(f"Updating ontology class (async): {class_id}")
            ontology_class = await self.get_by_id_async(class_id)
            if not ontology_class:
                logger.warning(f"Ontology class not found for update (async): {class_id}")
                return None
            if "class_name" in update_data and update_data["class_name"] is not None:
                ontology_class.class_name = update_data["class_name"]
            if "class_description" in update_data:
                ontology_class.class_description = update_data["class_description"]
            await self.db.flush()
            logger.info(f"Ontology class updated successfully (async): {class_id}")
            return ontology_class
        except Exception as e:
            logger.error(f"Failed to update ontology class (async): {str(e)}", exc_info=True)
            raise

    async def delete_async(self, class_id: UUID) -> bool:
        """Async version of delete"""
        try:
            logger.info(f"Deleting ontology class (async): {class_id}")
            ontology_class = await self.get_by_id_async(class_id)
            if not ontology_class:
                logger.warning(f"Ontology class not found for delete (async): {class_id}")
                return False
            await self.db.delete(ontology_class)
            await self.db.flush()
            logger.info(f"Ontology class deleted successfully (async): {class_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete ontology class (async): {str(e)}", exc_info=True)
            raise

    async def check_ownership_async(self, class_id: UUID, workspace_id: UUID) -> bool:
        """Async version of check_ownership"""
        try:
            logger.debug(
                f"Checking class ownership (async) - "
                f"class_id={class_id}, workspace_id={workspace_id}"
            )
            stmt = (
                select(func.count(OntologyClass.class_id))
                .join(OntologyScene, OntologyClass.scene_id == OntologyScene.scene_id)
                .where(
                    OntologyClass.class_id == class_id,
                    OntologyScene.workspace_id == workspace_id
                )
            )
            result = await self.db.execute(stmt)
            count = result.scalar() or 0
            is_owner = count > 0
            logger.debug(f"Class ownership check result (async): {is_owner} - class_id={class_id}")
            return is_owner
        except Exception as e:
            logger.error(f"Failed to check class ownership (async): {str(e)}", exc_info=True)
            raise

    async def get_scene_id_by_class_async(self, class_id: UUID) -> Optional[UUID]:
        """Async version of get_scene_id_by_class"""
        try:
            logger.debug(f"Getting scene ID by class (async): {class_id}")
            ontology_class = await self.get_by_id_async(class_id)
            if not ontology_class:
                logger.debug(f"Class not found (async): {class_id}")
                return None
            logger.debug(f"Found scene ID (async): {ontology_class.scene_id} for class: {class_id}")
            return ontology_class.scene_id
        except Exception as e:
            logger.error(f"Failed to get scene ID by class (async): {str(e)}", exc_info=True)
            raise

    async def count_by_scene_async(self, scene_id: UUID) -> int:
        """获取场景下的类型数量

        Args:
            scene_id: 场景ID

        Returns:
            int: 类型数量
        """
        try:
            stmt = select(func.count(OntologyClass.class_id)).where(OntologyClass.scene_id == scene_id)
            result = await self.db.execute(stmt)
            return result.scalar() or 0
        except Exception as e:
            logger.error(f"Failed to count classes by scene (async): {str(e)}", exc_info=True)
            raise
