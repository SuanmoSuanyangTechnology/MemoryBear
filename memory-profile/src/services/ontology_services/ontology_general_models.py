# -*- coding: utf-8 -*-
"""通用本体类型数据模型模块

本模块定义用于通用本体类型管理的数据结构，包括：
- OntologyFileFormat: 本体文件格式枚举
- GeneralOntologyType: 通用本体类型数据类
- GeneralOntologyTypeRegistry: 通用本体类型注册表

Classes:
    OntologyFileFormat: 本体文件格式枚举，支持 TTL、OWL/XML、RDF/XML、N-Triples、JSON-LD
    GeneralOntologyType: 通用本体类型，包含类名、URI、标签、描述、父类等信息
    GeneralOntologyTypeRegistry: 类型注册表，管理类型集合和层次结构
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class OntologyFileFormat(Enum):
    """本体文件格式枚举
    
    支持的格式：
    - TURTLE: Turtle 格式 (.ttl 文件)
    - RDF_XML: RDF/XML 格式 (.owl, .rdf 文件)
    - N_TRIPLES: N-Triples 格式 (.nt 文件)
    - JSON_LD: JSON-LD 格式 (.jsonld, .json 文件)
    """
    TURTLE = "turtle"      # .ttl 文件
    RDF_XML = "xml"        # .owl, .rdf (RDF/XML 格式)
    N_TRIPLES = "nt"       # .nt 文件
    JSON_LD = "json-ld"    # .jsonld 文件
    
    @classmethod
    def from_extension(cls, file_path: str) -> "OntologyFileFormat":
        """根据文件扩展名推断格式
        
        Args:
            file_path: 文件路径
            
        Returns:
            推断出的文件格式，默认返回 RDF_XML
        """
        ext = file_path.lower().split('.')[-1]
        format_map = {
            'ttl': cls.TURTLE,
            'owl': cls.RDF_XML,
            'rdf': cls.RDF_XML,
            'nt': cls.N_TRIPLES,
            'jsonld': cls.JSON_LD,
            'json': cls.JSON_LD,
        }
        return format_map.get(ext, cls.RDF_XML)


@dataclass
class GeneralOntologyType:
    """通用本体类型
    
    表示从本体文件中解析出的类型定义，包含类型的基本信息和层次关系。
    
    Attributes:
        class_name: 类型名称，如 "Person"
        class_uri: 完整 URI，如 "http://dbpedia.org/ontology/Person"
        labels: 多语言标签字典，键为语言代码（如 "en", "zh"），值为标签文本
        description: 类型描述
        parent_class: 父类名称，用于构建类型层次
        source_file: 来源文件路径
    """
    class_name: str                          # 类型名称，如 "Person"
    class_uri: str                           # 完整 URI
    labels: Dict[str, str] = field(default_factory=dict)  # 多语言标签
    description: Optional[str] = None        # 类型描述
    parent_class: Optional[str] = None       # 父类名称
    source_file: Optional[str] = None        # 来源文件
    
    def get_label(self, lang: str = "en") -> str:
        """获取指定语言的标签
        
        优先返回指定语言的标签，如果不存在则尝试返回英文标签，
        最后返回类型名称作为默认值。
        
        Args:
            lang: 语言代码，默认为 "en"
            
        Returns:
            指定语言的标签，或默认值
        """
        return self.labels.get(lang, self.labels.get("en", self.class_name))


@dataclass
class GeneralOntologyTypeRegistry:
    """通用本体类型注册表
    
    管理解析后的本体类型集合，提供类型查询、层次遍历、注册表合并等功能。
    
    Attributes:
        types: 类型字典，键为类型名称，值为 GeneralOntologyType 实例
        hierarchy: 层次结构字典，键为父类名称，值为子类名称集合
        source_files: 已加载的源文件路径列表
    """
    types: Dict[str, GeneralOntologyType] = field(default_factory=dict)
    hierarchy: Dict[str, Set[str]] = field(default_factory=dict)  # 父类 -> 子类集合
    source_files: List[str] = field(default_factory=list)
    
    def get_type(self, name: str) -> Optional[GeneralOntologyType]:
        """根据名称获取类型
        
        Args:
            name: 类型名称
            
        Returns:
            对应的 GeneralOntologyType 实例，如果不存在则返回 None
        """
        return self.types.get(name)
    
    def get_ancestors(self, name: str) -> List[str]:
        """获取类型的所有祖先类型（防循环）
        
        从当前类型开始，沿着父类链向上遍历，返回所有祖先类型名称。
        使用 visited 集合防止循环引用导致的无限循环。
        
        Args:
            name: 类型名称
            
        Returns:
            祖先类型名称列表，按从近到远的顺序排列
        """
        ancestors = []
        current = name
        visited = set()
        while current and current not in visited:
            visited.add(current)
            type_info = self.types.get(current)
            if type_info and type_info.parent_class:
                # 检测循环引用
                if type_info.parent_class in visited:
                    logger.warning(
                        f"检测到类型层次循环引用: {current} -> {type_info.parent_class}，"
                        f"已遍历路径: {' -> '.join([name] + ancestors)}"
                    )
                    break
                ancestors.append(type_info.parent_class)
                current = type_info.parent_class
            else:
                break
        return ancestors
    
    def get_descendants(self, name: str) -> Set[str]:
        """获取类型的所有后代类型
        
        从当前类型开始，沿着子类关系向下遍历，返回所有后代类型名称。
        使用广度优先搜索，避免重复处理已访问的类型。
        
        Args:
            name: 类型名称
            
        Returns:
            后代类型名称集合
        """
        descendants: Set[str] = set()
        to_process = [name]
        while to_process:
            current = to_process.pop()
            children = self.hierarchy.get(current, set())
            new_children = children - descendants
            descendants.update(new_children)
            to_process.extend(new_children)
        return descendants
    
    def merge(self, other: "GeneralOntologyTypeRegistry") -> None:
        """合并另一个注册表（先加载的优先）
        
        将另一个注册表的类型和层次结构合并到当前注册表。
        对于同名类型，保留当前注册表中已存在的定义（先加载优先）。
        层次结构会合并所有子类关系。
        
        Args:
            other: 要合并的另一个注册表
        """
        for name, type_info in other.types.items():
            if name not in self.types:
                self.types[name] = type_info
        for parent, children in other.hierarchy.items():
            if parent not in self.hierarchy:
                self.hierarchy[parent] = set()
            self.hierarchy[parent].update(children)
        self.source_files.extend(other.source_files)
    
    def get_statistics(self) -> Dict[str, Any]:
        """获取注册表统计信息
        
        Returns:
            包含以下键的字典：
            - total_types: 总类型数
            - root_types: 根类型数（无父类的类型）
            - max_depth: 类型层次的最大深度
            - source_files: 源文件列表
        """
        return {
            "total_types": len(self.types),
            "root_types": len([t for t in self.types.values() if not t.parent_class]),
            "max_depth": self._calculate_max_depth(),
            "source_files": self.source_files,
        }
    
    def _calculate_max_depth(self) -> int:
        """计算类型层次的最大深度
        
        遍历所有类型，计算每个类型到根的深度，返回最大值。
        
        Returns:
            类型层次的最大深度
        """
        max_depth = 0
        for type_name in self.types:
            depth = len(self.get_ancestors(type_name))
            max_depth = max(max_depth, depth)
        return max_depth
