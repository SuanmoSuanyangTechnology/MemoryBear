# -*- coding: utf-8 -*-
"""本体类型合并服务模块

本模块实现本体类型合并服务，负责按优先级合并场景类型与通用类型。

合并优先级：
1. 场景特定类型（最高优先级）
2. 核心通用类型
3. 相关父类类型（最低优先级）

Classes:
    OntologyTypeMerger: 本体类型合并服务类

Constants:
    DEFAULT_CORE_GENERAL_TYPES: 默认核心通用类型集合
"""

import logging
from typing import List, Optional, Set

from app.core.memory.models.ontology_general_models import GeneralOntologyTypeRegistry
from app.core.memory.models.ontology_extraction_models import OntologyTypeInfo, OntologyTypeList

logger = logging.getLogger(__name__)

# 默认核心通用类型
DEFAULT_CORE_GENERAL_TYPES: Set[str] = {
    "Person", "Organization", "Company", "GovernmentAgency",
    "Place", "Location", "City", "Country", "Building",
    "Event", "SportsEvent", "MusicEvent", "SocialEvent",
    "Work", "Book", "Film", "Software", "Album",
    "Concept", "TopicalConcept", "AcademicSubject",
    "Device", "Food", "Drug", "ChemicalSubstance",
    "TimePeriod", "Year",
}


class OntologyTypeMerger:
    """本体类型合并服务
    
    负责按优先级合并场景类型与通用类型，生成用于三元组提取的类型列表。
    
    合并优先级：
    1. 场景特定类型（最高优先级）- 标记为 [场景类型]
    2. 核心通用类型 - 标记为 [通用类型]
    3. 相关父类类型（最低优先级）- 标记为 [通用父类]
    
    Attributes:
        general_registry: 通用本体类型注册表
        max_types_in_prompt: Prompt 中最大类型数量限制
        core_types: 核心通用类型集合
    
    Example:
        >>> registry = GeneralOntologyTypeRegistry()
        >>> merger = OntologyTypeMerger(registry, max_types_in_prompt=50)
        >>> merged = merger.merge(scene_types)
        >>> print(len(merged.types))
    """
    
    def __init__(
        self,
        general_registry: GeneralOntologyTypeRegistry,
        max_types_in_prompt: int = 50,
        core_types: Optional[List[str]] = None
    ):
        """初始化本体类型合并服务
        
        Args:
            general_registry: 通用本体类型注册表
            max_types_in_prompt: Prompt 中最大类型数量，默认 50
            core_types: 自定义核心类型列表，如果为 None 则使用默认核心类型
        """
        self.general_registry = general_registry
        self.max_types_in_prompt = max_types_in_prompt
        self.core_types: Set[str] = set(core_types) if core_types else DEFAULT_CORE_GENERAL_TYPES.copy()
    
    def update_core_types(self, core_types: List[str]) -> None:
        """动态更新核心类型列表
        
        更新后立即生效，无需重启服务。
        
        Args:
            core_types: 新的核心类型列表
        """
        self.core_types = set(core_types)
        logger.info(f"核心类型已更新: {len(self.core_types)} 个类型")
    
    def merge(
        self,
        scene_types: Optional[OntologyTypeList],
        include_related_types: bool = True
    ) -> OntologyTypeList:
        """合并场景类型与通用类型
        
        按优先级合并类型：
        1. 场景特定类型（最高优先级）
        2. 核心通用类型
        3. 相关父类类型（可选）
        
        合并后的类型总数不超过 max_types_in_prompt。
        
        Args:
            scene_types: 场景特定类型列表，可以为 None
            include_related_types: 是否包含相关父类类型，默认 True
            
        Returns:
            合并后的类型列表，每个类型带有来源标记
        """
        merged_types: List[OntologyTypeInfo] = []
        seen_names: Set[str] = set()
        
        # 1. 场景特定类型（最高优先级）
        scene_type_count = 0
        if scene_types and scene_types.types:
            for scene_type in scene_types.types:
                if scene_type.class_name not in seen_names:
                    merged_types.append(OntologyTypeInfo(
                        class_name=scene_type.class_name,
                        class_description=f"[场景类型] {scene_type.class_description}"
                    ))
                    seen_names.add(scene_type.class_name)
                    scene_type_count += 1
        
        # 2. 核心通用类型
        remaining_slots = self.max_types_in_prompt - len(merged_types)
        core_types_added: List[OntologyTypeInfo] = []
        
        for type_name in self.core_types:
            if type_name not in seen_names and remaining_slots > 0:
                general_type = self.general_registry.get_type(type_name)
                if general_type:
                    description = (
                        general_type.labels.get("zh") or 
                        general_type.description or 
                        general_type.get_label("en") or 
                        type_name
                    )
                    core_types_added.append(OntologyTypeInfo(
                        class_name=type_name,
                        class_description=f"[通用类型] {description}"
                    ))
                    seen_names.add(type_name)
                    remaining_slots -= 1
        
        merged_types.extend(core_types_added)
        
        # 3. 相关父类类型
        related_types_added: List[OntologyTypeInfo] = []
        if include_related_types and scene_types and scene_types.types:
            for scene_type in scene_types.types:
                if remaining_slots <= 0:
                    break
                general_type = self.general_registry.get_type(scene_type.class_name)
                if general_type and general_type.parent_class:
                    parent_name = general_type.parent_class
                    if parent_name not in seen_names:
                        parent_type = self.general_registry.get_type(parent_name)
                        if parent_type:
                            description = (
                                parent_type.labels.get("zh") or 
                                parent_type.description or 
                                parent_name
                            )
                            related_types_added.append(OntologyTypeInfo(
                                class_name=parent_name,
                                class_description=f"[通用父类] {description}"
                            ))
                            seen_names.add(parent_name)
                            remaining_slots -= 1
        
        merged_types.extend(related_types_added)
        
        logger.info(
            f"类型合并完成: 场景类型 {scene_type_count} 个, "
            f"核心通用类型 {len(core_types_added)} 个, "
            f"相关类型 {len(related_types_added)} 个, "
            f"总计 {len(merged_types)} 个"
        )
        
        return OntologyTypeList(types=merged_types)
    
    def get_type_hierarchy_hint(self, type_name: str) -> Optional[str]:
        """获取类型的层次提示信息（最多 3 级）
        
        返回类型的继承链信息，格式为 "类型名 → 父类1 → 父类2 → 父类3"。
        
        Args:
            type_name: 类型名称
            
        Returns:
            层次提示字符串，如果类型不存在或没有父类则返回 None
        """
        general_type = self.general_registry.get_type(type_name)
        if not general_type:
            return None
        ancestors = self.general_registry.get_ancestors(type_name)
        if ancestors:
            # 限制最多 3 级祖先
            return f"{type_name} → {' → '.join(ancestors[:3])}"
        return None
    
    def get_merge_statistics(self, scene_types: Optional[OntologyTypeList]) -> dict:
        """获取合并统计信息
        
        执行合并操作并返回各类型来源的数量统计。
        
        Args:
            scene_types: 场景特定类型列表
            
        Returns:
            包含以下键的统计字典：
            - total_types: 合并后总类型数
            - scene_types: 场景类型数量
            - general_types: 通用类型数量
            - parent_types: 父类类型数量
            - available_core_types: 可用核心类型数量
            - registry_total_types: 注册表中总类型数
        """
        merged = self.merge(scene_types)
        scene_count = sum(1 for t in merged.types if "[场景类型]" in t.class_description)
        general_count = sum(1 for t in merged.types if "[通用类型]" in t.class_description)
        parent_count = sum(1 for t in merged.types if "[通用父类]" in t.class_description)
        
        return {
            "total_types": len(merged.types),
            "scene_types": scene_count,
            "general_types": general_count,
            "parent_types": parent_count,
            "available_core_types": len(self.core_types),
            "registry_total_types": len(self.general_registry.types),
        }
