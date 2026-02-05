# -*- coding: utf-8 -*-
"""本体类型加载服务模块

本模块提供本体类型注册表和合并服务的单例访问，实现懒加载和缓存管理。

主要功能：
- 单例模式的注册表获取
- 懒加载机制（首次访问时加载）
- 缓存管理（避免重复解析）
- 重新加载功能（实验模式）

Functions:
    get_general_ontology_registry: 获取通用本体类型注册表（单例，懒加载）
    get_ontology_type_merger: 获取类型合并服务实例
    reload_ontology_registry: 重新加载本体注册表（实验模式）

Example:
    >>> from app.core.memory.ontology_services.ontology_type_loader import (
    ...     get_general_ontology_registry,
    ...     get_ontology_type_merger,
    ... )
    >>> registry = get_general_ontology_registry()
    >>> print(f"已加载 {len(registry.types)} 个类型")
    >>> merger = get_ontology_type_merger()
    >>> merged_types = merger.merge(scene_types)
"""

import logging
import os
from typing import List, Optional

from app.core.memory.models.ontology_general_models import GeneralOntologyTypeRegistry
from app.core.memory.utils.ontology.ontology_parser import (
    MultiOntologyParser,
    OntologyParser,
)
from app.core.memory.ontology_services.ontology_type_merger import OntologyTypeMerger

logger = logging.getLogger(__name__)

# 模块级别的单例实例
_general_registry: Optional[GeneralOntologyTypeRegistry] = None
_type_merger: Optional[OntologyTypeMerger] = None


def is_general_ontology_enabled() -> bool:
    """检查通用本体类型功能是否启用
    
    从配置读取 ENABLE_GENERAL_ONTOLOGY_TYPES 环境变量，
    支持字符串 "true"/"false" 和布尔值。
    
    Returns:
        bool: 如果启用返回 True，否则返回 False
        
    Example:
        >>> if is_general_ontology_enabled():
        ...     merger = get_ontology_type_merger()
        ...     merged_types = merger.merge(scene_types)
        ... else:
        ...     # 仅使用场景类型
        ...     merged_types = scene_types
    """
    from app.core.config import settings
    
    enable_config = getattr(settings, 'ENABLE_GENERAL_ONTOLOGY_TYPES', True)
    
    # 支持字符串和布尔值
    if isinstance(enable_config, str):
        return enable_config.lower() in ('true', '1', 'yes', 'on')
    return bool(enable_config)


def get_general_ontology_registry() -> GeneralOntologyTypeRegistry:
    """获取通用本体类型注册表（单例，懒加载）
    
    首次调用时加载本体文件并创建注册表实例，后续调用返回缓存的实例。
    这确保了本体文件只被解析一次，避免重复的 I/O 和解析开销。
    
    Returns:
        GeneralOntologyTypeRegistry: 通用本体类型注册表实例
        
    Example:
        >>> registry = get_general_ontology_registry()
        >>> person_type = registry.get_type("Person")
        >>> if person_type:
        ...     print(f"Person 类型的父类: {person_type.parent_class}")
    """
    global _general_registry
    
    if _general_registry is None:
        _general_registry = _load_ontology_registry()
    
    return _general_registry


def _load_ontology_registry(
    file_paths: Optional[List[str]] = None
) -> GeneralOntologyTypeRegistry:
    """加载本体注册表
    
    从配置的本体文件路径加载并解析本体文件，构建类型注册表。
    支持相对路径和绝对路径，相对路径基于 api 目录解析。
    
    Args:
        file_paths: 本体文件路径列表，如果为 None 则从配置读取
        
    Returns:
        GeneralOntologyTypeRegistry: 加载后的类型注册表，如果没有找到文件则返回空注册表
    """
    from app.core.config import settings
    
    if file_paths is None:
        # 从配置读取本体文件路径，支持逗号分隔的多个文件
        ontology_files_config = getattr(settings, 'GENERAL_ONTOLOGY_FILES', None)
        if ontology_files_config is None:
            file_paths = ['General_purpose_entity.ttl']
        elif isinstance(ontology_files_config, str):
            # 支持逗号分隔的字符串配置
            file_paths = [f.strip() for f in ontology_files_config.split(',') if f.strip()]
        else:
            file_paths = list(ontology_files_config)
    
    # 计算基础目录（api 目录）
    # 当前文件路径: api/app/core/memory/ontology_services/ontology_type_loader.py
    # 需要回退到: api/
    base_dir = os.path.dirname(
        os.path.dirname(
            os.path.dirname(
                os.path.dirname(
                    os.path.dirname(__file__)
                )
            )
        )
    )
    
    absolute_paths: List[str] = []
    
    for file_path in file_paths:
        if os.path.isabs(file_path):
            abs_path = file_path
        else:
            abs_path = os.path.join(base_dir, file_path)
        
        if os.path.exists(abs_path):
            absolute_paths.append(abs_path)
        else:
            logger.warning(f"本体文件不存在: {abs_path}")
    
    if not absolute_paths:
        logger.warning("没有找到任何本体文件，返回空注册表")
        return GeneralOntologyTypeRegistry()
    
    # 根据文件数量选择解析器
    if len(absolute_paths) == 1:
        parser = OntologyParser(absolute_paths[0])
        registry = parser.parse()
    else:
        parser = MultiOntologyParser(absolute_paths)
        registry = parser.parse_all()
    
    logger.info(f"本体注册表加载完成: {len(registry.types)} 个类型")
    return registry


def reload_ontology_registry(
    file_paths: List[str],
    core_types: Optional[List[str]] = None,
    max_types: Optional[int] = None
) -> GeneralOntologyTypeRegistry:
    """重新加载本体注册表（实验模式）
    
    原子性地替换缓存的注册表和合并服务实例。用于实验模式下动态切换本体配置，
    无需重启服务即可测试不同本体组合的效果。
    
    Args:
        file_paths: 新的本体文件路径列表
        core_types: 自定义核心类型列表，如果为 None 则使用配置中的默认值
        max_types: Prompt 中最大类型数量，如果为 None 则使用配置中的默认值
        
    Returns:
        GeneralOntologyTypeRegistry: 重新加载后的类型注册表
        
    Example:
        >>> new_registry = reload_ontology_registry(
        ...     file_paths=["custom_ontology.ttl"],
        ...     core_types=["Person", "Organization"],
        ...     max_types=30
        ... )
        >>> print(f"重新加载了 {len(new_registry.types)} 个类型")
    """
    global _general_registry, _type_merger
    from app.core.config import settings
    
    logger.info(f"重新加载本体注册表: {file_paths}")
    
    # 原子性地替换注册表
    _general_registry = _load_ontology_registry(file_paths)
    
    # 原子性地替换合并服务
    _type_merger = OntologyTypeMerger(
        _general_registry,
        max_types_in_prompt=max_types or getattr(settings, 'MAX_ONTOLOGY_TYPES_IN_PROMPT', 50),
        core_types=core_types
    )
    
    return _general_registry


def get_ontology_type_merger() -> OntologyTypeMerger:
    """获取类型合并服务实例
    
    首次调用时创建合并服务实例，后续调用返回缓存的实例。
    合并服务依赖于注册表，如果注册表尚未加载，会先触发注册表的懒加载。
    
    Returns:
        OntologyTypeMerger: 类型合并服务实例
        
    Example:
        >>> merger = get_ontology_type_merger()
        >>> merged_types = merger.merge(scene_types)
        >>> print(f"合并后共 {len(merged_types.types)} 个类型")
    """
    global _type_merger
    from app.core.config import settings
    
    if _type_merger is None:
        registry = get_general_ontology_registry()
        
        # 从配置读取核心类型列表
        core_types_config = getattr(settings, 'CORE_GENERAL_TYPES', None)
        if isinstance(core_types_config, str):
            # 支持逗号分隔的字符串配置
            core_types = [t.strip() for t in core_types_config.split(',') if t.strip()]
        elif core_types_config is not None:
            core_types = list(core_types_config)
        else:
            core_types = None
        
        _type_merger = OntologyTypeMerger(
            registry,
            max_types_in_prompt=getattr(settings, 'MAX_ONTOLOGY_TYPES_IN_PROMPT', 50),
            core_types=core_types
        )
    
    return _type_merger


def clear_ontology_cache() -> None:
    """清除本体缓存
    
    清除缓存的注册表和合并服务实例，下次访问时会重新加载。
    主要用于测试场景。
    """
    global _general_registry, _type_merger
    _general_registry = None
    _type_merger = None
    logger.info("本体缓存已清除")
