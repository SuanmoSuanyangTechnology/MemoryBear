"""本体类型加载器

提供统一的本体类型加载逻辑，避免代码重复。

Functions:
    load_ontology_types_for_scene: 从数据库加载场景的本体类型
    is_general_ontology_enabled: 检查是否启用通用本体
"""

import logging
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def load_ontology_types_for_scene(
    scene_id: Optional[UUID],
    workspace_id: UUID,
    db: Session
) -> Optional["OntologyTypeList"]:
    """从数据库加载场景的本体类型
    
    统一的本体类型加载逻辑，用于替代各处重复的加载代码。
    
    Args:
        scene_id: 场景ID，如果为 None 则返回 None
        workspace_id: 工作空间ID
        db: 数据库会话
        
    Returns:
        OntologyTypeList 如果场景有类型定义，否则返回 None
        
    Examples:
        >>> ontology_types = load_ontology_types_for_scene(
        ...     scene_id=scene_uuid,
        ...     workspace_id=workspace_uuid,
        ...     db=db_session
        ... )
        >>> if ontology_types:
        ...     print(f"Loaded {len(ontology_types.types)} types")
    """
    if not scene_id:
        return None
    
    try:
        from app.core.memory.models.ontology_extraction_models import OntologyTypeList
        from app.repositories.ontology_class_repository import OntologyClassRepository
        
        # 查询场景的本体类型
        ontology_repo = OntologyClassRepository(db)
        ontology_classes = ontology_repo.get_classes_by_scene(
            scene_id=scene_id,
            workspace_id=workspace_id
        )
        
        if not ontology_classes:
            logger.info(f"No ontology types found for scene_id: {scene_id}")
            return None
        
        # 转换为 OntologyTypeList
        ontology_types = OntologyTypeList.from_db_models(ontology_classes)
        logger.info(
            f"Loaded {len(ontology_types.types)} ontology types for scene_id: {scene_id}"
        )
        
        return ontology_types
        
    except Exception as e:
        logger.error(f"Failed to load ontology types for scene_id {scene_id}: {e}", exc_info=True)
        return None


def create_empty_ontology_type_list() -> Optional["OntologyTypeList"]:
    """创建空的本体类型列表（用于仅使用通用类型的场景）
    
    Returns:
        空的 OntologyTypeList 如果通用本体已启用，否则返回 None
    """
    try:
        from app.core.memory.models.ontology_extraction_models import OntologyTypeList
        
        if is_general_ontology_enabled():
            logger.info("Creating empty OntologyTypeList for general types only")
            return OntologyTypeList(types=[])
        
        return None
        
    except Exception as e:
        logger.warning(f"Failed to create empty OntologyTypeList: {e}")
        return None


def is_general_ontology_enabled() -> bool:
    """检查是否启用了通用本体
    
    Returns:
        True 如果通用本体已启用，否则 False
    """
    try:
        from app.core.memory.ontology_services.ontology_type_merger import OntologyTypeMerger
        
        merger = OntologyTypeMerger()
        return merger.general_registry is not None
        
    except Exception as e:
        logger.warning(f"Failed to check general ontology status: {e}")
        return False


def load_ontology_types_with_fallback(
    scene_id: Optional[UUID],
    workspace_id: UUID,
    db: Session,
    enable_general_fallback: bool = True
) -> Optional["OntologyTypeList"]:
    """加载本体类型，如果场景没有类型则回退到通用类型
    
    这是一个便捷函数，组合了场景类型加载和通用类型回退逻辑。
    
    Args:
        scene_id: 场景ID
        workspace_id: 工作空间ID
        db: 数据库会话
        enable_general_fallback: 是否在没有场景类型时启用通用类型回退
        
    Returns:
        OntologyTypeList 或 None
    """
    # 首先尝试加载场景类型
    ontology_types = load_ontology_types_for_scene(
        scene_id=scene_id,
        workspace_id=workspace_id,
        db=db
    )
    
    # 如果没有场景类型且启用了回退，创建空列表以使用通用类型
    if ontology_types is None and enable_general_fallback:
        ontology_types = create_empty_ontology_type_list()
        if ontology_types:
            logger.info("No scene ontology types, will use general ontology types only")
    
    return ontology_types
