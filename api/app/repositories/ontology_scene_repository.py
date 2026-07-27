# -*- coding: utf-8 -*-
"""本体场景Repository层

本模块提供本体场景的数据访问层实现。

Classes:
    OntologySceneRepository: 本体场景数据访问类
"""

from typing import List, Optional
from uuid import UUID

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, joinedload, selectinload

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
            db: SQLAlchemy数据库会话 (支持同步和异步)
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
    
    # ==================== Async 版本 ====================

    async def create_async(self, scene_data: dict, workspace_id: UUID) -> OntologyScene:
        """Async version of create"""
        try:
            logger.info(
                f"Creating ontology scene (async) - "
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
            logger.info(f"Ontology scene created successfully (async): {scene.scene_id}")
            return scene
        except Exception as e:
            logger.error(f"Failed to create ontology scene (async): {str(e)}", exc_info=True)
            raise

    async def get_by_id_async(self, scene_id: UUID) -> Optional[OntologyScene]:
        """Async version of get_by_id"""
        try:
            logger.debug(f"Getting ontology scene by ID (async): {scene_id}")
            stmt = select(OntologyScene).where(OntologyScene.scene_id == scene_id)
            result = await self.db.execute(stmt)
            scene = result.scalar_one_or_none()
            if scene:
                logger.debug(f"Ontology scene found (async): {scene_id}")
            else:
                logger.debug(f"Ontology scene not found (async): {scene_id}")
            return scene
        except Exception as e:
            logger.error(f"Failed to get ontology scene by ID (async): {str(e)}", exc_info=True)
            raise

    async def get_by_name_async(self, scene_name: str, workspace_id: UUID) -> Optional[OntologyScene]:
        """Async version of get_by_name"""
        try:
            logger.debug(
                f"Getting ontology scene by name (async) - "
                f"scene_name={scene_name}, workspace_id={workspace_id}"
            )
            stmt = (
                select(OntologyScene)
                .options(selectinload(OntologyScene.classes))
                .where(
                    OntologyScene.scene_name == scene_name,
                    OntologyScene.workspace_id == workspace_id
                )
            )
            result = await self.db.execute(stmt)
            scene = result.unique().scalar_one_or_none()
            if scene:
                logger.debug(f"Ontology scene found (async): {scene_name}")
            else:
                logger.debug(f"Ontology scene not found (async): {scene_name}")
            return scene
        except Exception as e:
            logger.error(f"Failed to get ontology scene by name (async): {str(e)}", exc_info=True)
            raise

    async def search_by_name_async(self, keyword: str, workspace_id: UUID) -> List[OntologyScene]:
        """Async version of search_by_name"""
        try:
            logger.debug(
                f"Searching ontology scenes by keyword (async) - "
                f"keyword={keyword}, workspace_id={workspace_id}"
            )
            stmt = (
                select(OntologyScene)
                .options(selectinload(OntologyScene.classes))
                .where(
                    OntologyScene.scene_name.ilike(f"%{keyword}%"),
                    OntologyScene.workspace_id == workspace_id
                )
                .order_by(OntologyScene.updated_at.desc())
            )
            result = await self.db.execute(stmt)
            scenes = result.unique().scalars().all()
            logger.info(
                f"Found {len(scenes)} ontology scenes matching keyword '{keyword}' "
                f"in workspace {workspace_id} (async)"
            )
            return list(scenes)
        except Exception as e:
            logger.error(f"Failed to search ontology scenes by keyword (async): {str(e)}", exc_info=True)
            raise

    async def get_by_workspace_async(self, workspace_id: UUID, page: Optional[int] = None, page_size: Optional[int] = None) -> tuple:
        """Async version of get_by_workspace"""
        try:
            logger.debug(f"Getting ontology scenes by workspace (async): {workspace_id}, page={page}, page_size={page_size}")

            # 总数查询
            count_stmt = (
                select(func.count(OntologyScene.scene_id))
                .where(OntologyScene.workspace_id == workspace_id)
            )
            count_result = await self.db.execute(count_stmt)
            total = count_result.scalar() or 0

            # 数据查询
            stmt = (
                select(OntologyScene)
                .options(selectinload(OntologyScene.classes))
                .where(OntologyScene.workspace_id == workspace_id)
                .order_by(OntologyScene.updated_at.desc())
            )
            if page is not None and page_size is not None:
                offset = (page - 1) * page_size
                stmt = stmt.offset(offset).limit(page_size)
                logger.debug(f"Applying pagination (async): offset={offset}, limit={page_size}")

            result = await self.db.execute(stmt)
            scenes = result.unique().scalars().all()
            logger.info(
                f"Found {len(scenes)} ontology scenes (total: {total}) in workspace {workspace_id} (async)"
            )
            return list(scenes), total
        except Exception as e:
            logger.error(f"Failed to get ontology scenes by workspace (async): {str(e)}", exc_info=True)
            raise

    async def update_async(self, scene_id: UUID, update_data: dict) -> Optional[OntologyScene]:
        """Async version of update"""
        try:
            logger.info(f"Updating ontology scene (async): {scene_id}")
            scene = await self.get_by_id_async(scene_id)
            if not scene:
                logger.warning(f"Ontology scene not found for update (async): {scene_id}")
                return None
            if "scene_name" in update_data and update_data["scene_name"] is not None:
                scene.scene_name = update_data["scene_name"]
            if "scene_description" in update_data:
                scene.scene_description = update_data["scene_description"]
            await self.db.flush()
            logger.info(f"Ontology scene updated successfully (async): {scene_id}")
            return scene
        except Exception as e:
            logger.error(f"Failed to update ontology scene (async): {str(e)}", exc_info=True)
            raise

    async def delete_async(self, scene_id: UUID) -> bool:
        """Async version of delete"""
        try:
            logger.info(f"Deleting ontology scene (async): {scene_id}")
            scene = await self.get_by_id_async(scene_id)
            if not scene:
                logger.warning(f"Ontology scene not found for delete (async): {scene_id}")
                return False
            await self.db.delete(scene)
            await self.db.flush()
            logger.info(f"Ontology scene deleted successfully (async): {scene_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete ontology scene (async): {str(e)}", exc_info=True)
            raise

    async def check_ownership_async(self, scene_id: UUID, workspace_id: UUID) -> bool:
        """Async version of check_ownership"""
        try:
            logger.debug(
                f"Checking scene ownership (async) - "
                f"scene_id={scene_id}, workspace_id={workspace_id}"
            )
            from sqlalchemy import or_
            stmt = (
                select(func.count(OntologyScene.scene_id))
                .where(
                    OntologyScene.scene_id == scene_id,
                    or_(
                        OntologyScene.workspace_id == workspace_id,
                        OntologyScene.is_system_default == True
                    )
                )
            )
            result = await self.db.execute(stmt)
            count = result.scalar() or 0
            is_owner = count > 0
            logger.debug(f"Scene ownership check result (async): {is_owner} - scene_id={scene_id}")
            return is_owner
        except Exception as e:
            logger.error(f"Failed to check scene ownership (async): {str(e)}", exc_info=True)
            raise

    async def get_simple_list_async(self, workspace_id: UUID) -> List[dict]:
        """Async version of get_simple_list"""
        try:
            logger.debug(f"Getting simple scene list (async) for workspace: {workspace_id}")
            stmt = (
                select(OntologyScene.scene_id, OntologyScene.scene_name)
                .where(OntologyScene.workspace_id == workspace_id)
                .order_by(OntologyScene.updated_at.desc())
            )
            result = await self.db.execute(stmt)
            rows = result.all()
            scenes = [
                {"scene_id": str(r.scene_id), "scene_name": r.scene_name}
                for r in rows
            ]
            logger.info(f"Found {len(scenes)} scenes (simple list) in workspace {workspace_id} (async)")
            return scenes
        except Exception as e:
            logger.error(f"Failed to get simple scene list (async): {str(e)}", exc_info=True)
            raise
