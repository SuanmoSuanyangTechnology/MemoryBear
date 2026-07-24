# -*- coding: utf-8 -*-
"""本体场景Repository层

本模块提供本体场景的数据访问层实现。

Classes:
    OntologySceneRepository: 本体场景数据访问类
"""

import logging
from typing import List, Optional
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, joinedload

from app.core.logging_config import get_db_logger
from app.models.ontology_scene import OntologyScene


logger = get_db_logger()


class OntologySceneRepository:
    """本体场景Repository
    
    提供本体场景的CRUD操作和权限检查。
    
    Attributes:
        db: SQLAlchemy数据库会话
    """
    
    def __init__(self, db: Session | AsyncSession):
        """初始化Repository
        
        Args:
            db: SQLAlchemy数据库会话
        """
        self.db = db
    
    def create(self, scene_data: dict, workspace_id: UUID) -> OntologyScene:
        """创建本体场景
        
        Args:
            scene_data: 场景数据字典，包含scene_name和scene_description
            workspace_id: 所属工作空间ID
            
        Returns:
            OntologyScene: 创建的场景对象
            
        Raises:
            Exception: 数据库操作失败
            
        Examples:
            >>> repo = OntologySceneRepository(db)
            >>> scene = repo.create(
            ...     {"scene_name": "医疗场景", "scene_description": "描述"},
            ...     workspace_id
            ... )
        """
        try:
            logger.info(
                f"Creating ontology scene - "
                f"name={scene_data.get('scene_name')}, "
                f"workspace_id={workspace_id}"
            )
            
            scene = OntologyScene(
                scene_name=scene_data.get("scene_name"),
                scene_description=scene_data.get("scene_description"),
                workspace_id=workspace_id
            )
            
            self.db.add(scene)
            self.db.flush()  # 获取ID但不提交
            
            logger.info(
                f"Ontology scene created successfully - "
                f"scene_id={scene.scene_id}"
            )
            
            return scene
            
        except Exception as e:
            logger.error(
                f"Failed to create ontology scene: {str(e)}",
                exc_info=True
            )
            raise
    
    async def create_async(self, scene_data: dict, workspace_id: UUID) -> OntologyScene:
        """创建本体场景（异步版本）
        
        Args:
            db: 异步数据库会话
            scene_data: 场景数据字典，包含scene_name和scene_description
            workspace_id: 所属工作空间ID
            
        Returns:
            OntologyScene: 创建的场景对象
            
        Raises:
            Exception: 数据库操作失败
        """
        try:
            logger.info(
                f"Creating ontology scene - "
                f"name={scene_data.get('scene_name')}, "
                f"workspace_id={workspace_id}"
            )
            
            scene = OntologyScene(
                scene_name=scene_data.get("scene_name"),
                scene_description=scene_data.get("scene_description"),
                workspace_id=workspace_id
            )
            
            self.db.add(scene)
            await self.db.flush()
            
            logger.info(
                f"Ontology scene created successfully - "
                f"scene_id={scene.scene_id}"
            )
            
            return scene
            
        except Exception as e:
            logger.error(
                f"Failed to create ontology scene: {str(e)}",
                exc_info=True
            )
            raise
    
    def get_by_id(self, scene_id: UUID) -> Optional[OntologyScene]:
        """根据ID获取场景
        
        Args:
            scene_id: 场景ID
            
        Returns:
            Optional[OntologyScene]: 场景对象，不存在则返回None
            
        Examples:
            >>> repo = OntologySceneRepository(db)
            >>> scene = repo.get_by_id(scene_id)
        """
        try:
            logger.debug(f"Getting ontology scene by ID: {scene_id}")
            
            scene = self.db.query(OntologyScene).filter(
                OntologyScene.scene_id == scene_id
            ).first()
            
            if scene:
                logger.debug(f"Ontology scene found: {scene_id}")
            else:
                logger.debug(f"Ontology scene not found: {scene_id}")
            
            return scene
            
        except Exception as e:
            logger.error(
                f"Failed to get ontology scene by ID: {str(e)}",
                exc_info=True
            )
            raise
    
    async def get_by_id_async(self, scene_id: UUID) -> Optional[OntologyScene]:
        """根据ID获取场景（异步版本）
        
        Args:
            db: 异步数据库会话
            scene_id: 场景ID
            
        Returns:
            Optional[OntologyScene]: 场景对象，不存在则返回None
        """
        try:
            logger.debug(f"Getting ontology scene by ID: {scene_id}")
            
            result = await self.db.execute(
                select(OntologyScene).where(OntologyScene.scene_id == scene_id)
            )
            scene = result.scalars().first()
            
            if scene:
                logger.debug(f"Ontology scene found: {scene_id}")
            else:
                logger.debug(f"Ontology scene not found: {scene_id}")
            
            return scene
            
        except Exception as e:
            logger.error(
                f"Failed to get ontology scene by ID: {str(e)}",
                exc_info=True
            )
            raise
    
    def get_by_name(self, scene_name: str, workspace_id: UUID) -> Optional[OntologyScene]:
        """根据场景名称和工作空间ID获取场景（精确匹配）
        
        Args:
            scene_name: 场景名称
            workspace_id: 工作空间ID
            
        Returns:
            Optional[OntologyScene]: 场景对象，不存在则返回None
            
        Examples:
            >>> repo = OntologySceneRepository(db)
            >>> scene = repo.get_by_name("医疗场景", workspace_id)
        """
        try:
            logger.debug(
                f"Getting ontology scene by name - "
                f"scene_name={scene_name}, workspace_id={workspace_id}"
            )
            
            scene = self.db.query(OntologyScene).options(
                joinedload(OntologyScene.classes)
            ).filter(
                OntologyScene.scene_name == scene_name,
                OntologyScene.workspace_id == workspace_id
            ).first()
            
            if scene:
                logger.debug(f"Ontology scene found: {scene_name}")
            else:
                logger.debug(f"Ontology scene not found: {scene_name}")
            
            return scene
            
        except Exception as e:
            logger.error(
                f"Failed to get ontology scene by name: {str(e)}",
                exc_info=True
            )
            raise
    
    async def get_by_name_async(self, scene_name: str, workspace_id: UUID) -> Optional[OntologyScene]:
        """根据场景名称和工作空间ID获取场景（精确匹配，异步版本）
        
        Args:
            db: 异步数据库会话
            scene_name: 场景名称
            workspace_id: 工作空间ID
            
        Returns:
            Optional[OntologyScene]: 场景对象，不存在则返回None
        """
        try:
            logger.debug(
                f"Getting ontology scene by name - "
                f"scene_name={scene_name}, workspace_id={workspace_id}"
            )
            
            result = await self.db.execute(
                select(OntologyScene)
                .options(joinedload(OntologyScene.classes))
                .where(
                    OntologyScene.scene_name == scene_name,
                    OntologyScene.workspace_id == workspace_id
                )
            )
            scene = result.unique().scalars().first()
            
            if scene:
                logger.debug(f"Ontology scene found: {scene_name}")
            else:
                logger.debug(f"Ontology scene not found: {scene_name}")
            
            return scene
            
        except Exception as e:
            logger.error(
                f"Failed to get ontology scene by name: {str(e)}",
                exc_info=True
            )
            raise
    
    async def search_by_name_async(self, keyword: str, workspace_id: UUID) -> List[OntologyScene]:
        """根据关键词模糊搜索场景（异步版本）
        
        使用 LIKE 进行模糊匹配，支持中文和英文。
        
        Args:
            db: 异步数据库会话
            keyword: 搜索关键词
            workspace_id: 工作空间ID
            
        Returns:
            List[OntologyScene]: 匹配的场景列表
        """
        try:
            logger.debug(
                f"Searching ontology scenes by keyword - "
                f"keyword={keyword}, workspace_id={workspace_id}"
            )
            
            result = await self.db.execute(
                select(OntologyScene)
                .options(joinedload(OntologyScene.classes))
                .where(
                    OntologyScene.scene_name.ilike(f"%{keyword}%"),
                    OntologyScene.workspace_id == workspace_id
                )
                .order_by(OntologyScene.updated_at.desc())
            )
            scenes = result.unique().scalars().all()
            
            logger.info(
                f"Found {len(scenes)} ontology scenes matching keyword '{keyword}' "
                f"in workspace {workspace_id}"
            )
            
            return scenes
            
        except Exception as e:
            logger.error(
                f"Failed to search ontology scenes by keyword: {str(e)}",
                exc_info=True
            )
            raise
    
    async def get_by_workspace_async(self, workspace_id: UUID, page: Optional[int] = None, page_size: Optional[int] = None) -> tuple:
        """获取工作空间下的所有场景（支持分页，异步版本）
        
        使用joinedload预加载classes关系以统计数量。
        
        Args:
            db: 异步数据库会话
            workspace_id: 工作空间ID
            page: 页码（可选，从1开始）
            page_size: 每页数量（可选）
            
        Returns:
            tuple: (场景列表, 总数量)
        """
        try:
            logger.debug(f"Getting ontology scenes by workspace: {workspace_id}, page={page}, page_size={page_size}")
            
            # 获取总数
            count_result = await self.db.execute(
                select(func.count()).select_from(OntologyScene).where(
                    OntologyScene.workspace_id == workspace_id
                )
            )
            total = count_result.scalar()
            
            # 构建查询
            stmt = (
                select(OntologyScene)
                .options(joinedload(OntologyScene.classes))
                .where(OntologyScene.workspace_id == workspace_id)
                .order_by(OntologyScene.updated_at.desc())
            )
            
            # 如果提供了分页参数，应用分页
            if page is not None and page_size is not None:
                offset = (page - 1) * page_size
                stmt = stmt.offset(offset).limit(page_size)
                logger.debug(f"Applying pagination: offset={offset}, limit={page_size}")
            
            result = await self.db.execute(stmt)
            scenes = result.unique().scalars().all()
            
            logger.info(
                f"Found {len(scenes)} ontology scenes (total: {total}) in workspace {workspace_id}"
            )
            
            return scenes, total
            
        except Exception as e:
            logger.error(
                f"Failed to get ontology scenes by workspace: {str(e)}",
                exc_info=True
            )
            raise
    
    async def update_async(self, scene_id: UUID, update_data: dict) -> Optional[OntologyScene]:
        """更新场景信息（异步版本）
        
        Args:
            db: 异步数据库会话
            scene_id: 场景ID
            update_data: 更新数据字典
            
        Returns:
            Optional[OntologyScene]: 更新后的场景对象，不存在则返回None
            
        Raises:
            Exception: 数据库操作失败
        """
        try:
            logger.info(f"Updating ontology scene: {scene_id}")
            
            scene = await self.get_by_id_async(scene_id)
            if not scene:
                logger.warning(f"Ontology scene not found for update: {scene_id}")
                return None
            
            # 更新字段
            if "scene_name" in update_data and update_data["scene_name"] is not None:
                scene.scene_name = update_data["scene_name"]
            
            if "scene_description" in update_data:
                scene.scene_description = update_data["scene_description"]
            
            await self.db.flush()
            
            logger.info(f"Ontology scene updated successfully: {scene_id}")
            
            return scene
            
        except Exception as e:
            logger.error(
                f"Failed to update ontology scene: {str(e)}",
                exc_info=True
            )
            raise
    
    async def delete_async(self, scene_id: UUID) -> bool:
        """删除场景（级联删除类型，异步版本）
        
        依赖数据库级联删除配置（ondelete="CASCADE"）。
        
        Args:
            db: 异步数据库会话
            scene_id: 场景ID
            
        Returns:
            bool: 删除成功返回True，场景不存在返回False
            
        Raises:
            Exception: 数据库操作失败
        """
        try:
            logger.info(f"Deleting ontology scene: {scene_id}")
            
            scene = await self.get_by_id_async(scene_id)
            if not scene:
                logger.warning(f"Ontology scene not found for delete: {scene_id}")
                return False
            
            await self.db.delete(scene)
            await self.db.flush()
            
            logger.info(
                f"Ontology scene deleted successfully (cascade): {scene_id}"
            )
            
            return True
            
        except Exception as e:
            logger.error(
                f"Failed to delete ontology scene: {str(e)}",
                exc_info=True
            )
            raise
    
    async def check_ownership_async(self, scene_id: UUID, workspace_id: UUID) -> bool:
        """检查场景是否属于指定工作空间（异步版本）
        
        Args:
            db: 异步数据库会话
            scene_id: 场景ID
            workspace_id: 工作空间ID
            
        Returns:
            bool: 属于返回True，否则返回False
        """
        try:
            logger.debug(
                f"Checking scene ownership - "
                f"scene_id={scene_id}, workspace_id={workspace_id}"
            )
            
            count_result = await self.db.execute(
                select(func.count()).select_from(OntologyScene).where(
                    OntologyScene.scene_id == scene_id,
                    or_(OntologyScene.workspace_id == workspace_id, OntologyScene.is_system_default == True)
                )
            )
            count = count_result.scalar()
            
            is_owner = count > 0
            
            logger.debug(
                f"Scene ownership check result: {is_owner} - "
                f"scene_id={scene_id}"
            )
            
            return is_owner
            
        except Exception as e:
            logger.error(
                f"Failed to check scene ownership: {str(e)}",
                exc_info=True
            )
            raise
    
    async def get_simple_list_async(self, workspace_id: UUID) -> List[dict]:
        """获取场景简单列表（仅包含scene_id和scene_name，用于下拉选择，异步版本）
        
        这是一个轻量级查询，不加载关联的classes，响应速度快。
        
        Args:
            db: 异步数据库会话
            workspace_id: 工作空间ID
            
        Returns:
            List[dict]: 场景简单列表，每项包含scene_id和scene_name
        """
        try:
            logger.debug(f"Getting simple scene list for workspace: {workspace_id}")
            
            result = await self.db.execute(
                select(OntologyScene.scene_id, OntologyScene.scene_name)
                .where(OntologyScene.workspace_id == workspace_id)
                .order_by(OntologyScene.updated_at.desc())
            )
            rows = result.all()
            
            scenes = [
                {"scene_id": str(r.scene_id), "scene_name": r.scene_name}
                for r in rows
            ]
            
            logger.info(f"Found {len(scenes)} scenes (simple list) in workspace {workspace_id}")
            
            return scenes
            
        except Exception as e:
            logger.error(
                f"Failed to get simple scene list: {str(e)}",
                exc_info=True
            )
            raise
