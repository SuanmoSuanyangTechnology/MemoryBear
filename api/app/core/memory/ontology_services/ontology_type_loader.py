"""本体类型加载器

提供统一的本体类型加载逻辑，避免代码重复。

Functions:
    load_ontology_types_for_scene: 从数据库加载场景的本体类型
    is_general_ontology_enabled: 检查是否启用通用本体
    get_general_ontology_registry: 获取通用本体类型注册表（单例，懒加载）
    get_ontology_type_merger: 获取类型合并服务实例
    reload_ontology_registry: 重新加载本体注册表
    clear_ontology_cache: 清除本体缓存
"""

import logging
import os
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# 模块级缓存（单例）
_general_registry_cache = None
_ontology_type_merger_cache = None


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
            scene_id=scene_id
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
    
    通过配置开关和注册表是否可用来判断。
    
    Returns:
        True 如果通用本体已启用，否则 False
    """
    try:
        from app.core.config import settings
        
        if not settings.ENABLE_GENERAL_ONTOLOGY_TYPES:
            return False
        
        registry = get_general_ontology_registry()
        return registry is not None and len(registry.types) > 0
        
    except Exception as e:
        logger.warning(f"Failed to check general ontology status: {e}")
        return False


def get_general_ontology_registry():
    """获取通用本体类型注册表（单例，懒加载）
    
    从配置的本体文件中解析并缓存注册表。
    
    Returns:
        GeneralOntologyTypeRegistry 实例，如果加载失败则返回 None
    """
    global _general_registry_cache
    
    if _general_registry_cache is not None:
        return _general_registry_cache
    
    try:
        from app.core.config import settings
        
        if not settings.ENABLE_GENERAL_ONTOLOGY_TYPES:
            logger.info("通用本体类型功能已禁用")
            return None
        
        # 解析本体文件路径
        file_names = [f.strip() for f in settings.GENERAL_ONTOLOGY_FILES.split(",") if f.strip()]
        if not file_names:
            logger.warning("未配置通用本体文件")
            return None
        
        # 构建完整路径（相对于项目根目录）
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
        file_paths = []
        for name in file_names:
            full_path = os.path.join(base_dir, name)
            if os.path.exists(full_path):
                file_paths.append(full_path)
            else:
                logger.warning(f"本体文件不存在: {full_path}")
        
        if not file_paths:
            logger.warning("没有找到可用的通用本体文件")
            return None
        
        # 解析本体文件
        from app.core.memory.utils.ontology.ontology_parser import MultiOntologyParser
        
        parser = MultiOntologyParser(file_paths)
        _general_registry_cache = parser.parse_all()
        logger.info(f"通用本体注册表加载完成: {len(_general_registry_cache.types)} 个类型")
        
        return _general_registry_cache
        
    except Exception as e:
        logger.error(f"加载通用本体注册表失败: {e}", exc_info=True)
        return None


def get_ontology_type_merger():
    """获取类型合并服务实例（单例，懒加载）
    
    Returns:
        OntologyTypeMerger 实例，如果通用本体未启用则返回 None
    """
    global _ontology_type_merger_cache
    
    if _ontology_type_merger_cache is not None:
        return _ontology_type_merger_cache
    
    try:
        registry = get_general_ontology_registry()
        if registry is None:
            return None
        
        from app.core.config import settings
        from app.core.memory.ontology_services.ontology_type_merger import OntologyTypeMerger
        
        # 从配置读取核心类型
        core_types_str = settings.CORE_GENERAL_TYPES
        core_types = [t.strip() for t in core_types_str.split(",") if t.strip()] if core_types_str else None
        
        _ontology_type_merger_cache = OntologyTypeMerger(
            general_registry=registry,
            max_types_in_prompt=settings.MAX_ONTOLOGY_TYPES_IN_PROMPT,
            core_types=core_types,
        )
        logger.info("OntologyTypeMerger 实例创建完成")
        
        return _ontology_type_merger_cache
        
    except Exception as e:
        logger.error(f"创建 OntologyTypeMerger 失败: {e}", exc_info=True)
        return None


def reload_ontology_registry():
    """重新加载本体注册表（清除缓存后重新加载）
    
    用于实验模式下动态更新本体配置。
    """
    clear_ontology_cache()
    registry = get_general_ontology_registry()
    if registry:
        get_ontology_type_merger()
        logger.info("本体注册表已重新加载")
    return registry


def clear_ontology_cache():
    """清除本体缓存"""
    global _general_registry_cache, _ontology_type_merger_cache
    _general_registry_cache = None
    _ontology_type_merger_cache = None
    logger.info("本体缓存已清除")


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
