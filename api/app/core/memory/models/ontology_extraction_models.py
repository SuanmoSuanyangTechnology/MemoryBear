# -*- coding: utf-8 -*-
"""本体类型数据结构模块

本模块定义用于在萃取流程中传递本体类型信息的轻量级数据类。

Classes:
    OntologyTypeInfo: 单个本体类型信息
    OntologyTypeList: 本体类型列表
"""

from dataclasses import dataclass
from typing import List


@dataclass
class OntologyTypeInfo:
    """本体类型信息，用于萃取流程中传递。
    
    Attributes:
        class_name: 类型名称
        class_description: 类型描述
    """
    class_name: str
    class_description: str
    
    def to_prompt_format(self) -> str:
        """转换为提示词格式。
        
        Returns:
            格式化的字符串，如 "- TypeName: Description"
        """
        return f"- {self.class_name}: {self.class_description}"


@dataclass
class OntologyTypeList:
    """本体类型列表。
    
    Attributes:
        types: 本体类型信息列表
    """
    types: List[OntologyTypeInfo]
    
    @classmethod
    def from_db_models(cls, ontology_classes: list) -> "OntologyTypeList":
        """从数据库模型转换创建 OntologyTypeList。
        
        Args:
            ontology_classes: OntologyClass 数据库模型列表，
                每个对象应包含 class_name 和 class_description 属性
        
        Returns:
            包含转换后类型信息的 OntologyTypeList 实例
        """
        types = [
            OntologyTypeInfo(
                class_name=oc.class_name,
                class_description=oc.class_description or ""
            )
            for oc in ontology_classes
        ]
        return cls(types=types)
    
    def to_prompt_section(self) -> str:
        """转换为提示词中的类型列表部分。
        
        Returns:
            格式化的类型列表字符串，每行一个类型；
            如果列表为空则返回空字符串
        """
        if not self.types:
            return ""
        lines = [t.to_prompt_format() for t in self.types]
        return "\n".join(lines)
    
    def get_type_names(self) -> List[str]:
        """获取所有类型名称列表。
        
        Returns:
            类型名称字符串列表
        """
        return [t.class_name for t in self.types]
