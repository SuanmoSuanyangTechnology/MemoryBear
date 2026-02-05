# -*- coding: utf-8 -*-
"""本体类型服务模块

本模块提供本体类型相关的服务，包括：
- OntologyTypeMerger: 本体类型合并服务
- get_general_ontology_registry: 获取通用本体类型注册表（单例，懒加载）
- get_ontology_type_merger: 获取类型合并服务实例
- reload_ontology_registry: 重新加载本体注册表（实验模式）
- clear_ontology_cache: 清除本体缓存
- is_general_ontology_enabled: 检查通用本体类型功能是否启用
"""

from .ontology_type_merger import OntologyTypeMerger, DEFAULT_CORE_GENERAL_TYPES
from .ontology_type_loader import (
    get_general_ontology_registry,
    get_ontology_type_merger,
    reload_ontology_registry,
    clear_ontology_cache,
    is_general_ontology_enabled,
)

__all__ = [
    "OntologyTypeMerger",
    "DEFAULT_CORE_GENERAL_TYPES",
    "get_general_ontology_registry",
    "get_ontology_type_merger",
    "reload_ontology_registry",
    "clear_ontology_cache",
    "is_general_ontology_enabled",
]
