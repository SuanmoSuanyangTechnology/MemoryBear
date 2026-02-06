# -*- coding: utf-8 -*-
"""本体文件解析器模块

本模块提供统一的本体文件解析功能，支持多种 RDF 格式：
- Turtle (.ttl)
- OWL/XML (.owl)
- RDF/XML (.rdf)
- N-Triples (.nt)
- JSON-LD (.jsonld)

解析器会自动根据文件扩展名推断格式，并在解析失败时尝试其他格式。
解析结果包含类定义的名称、URI、多语言标签、描述和父类信息。

Classes:
    OntologyParser: 统一本体文件解析器
    MultiOntologyParser: 多本体文件解析器

Example:
    >>> parser = OntologyParser("ontology.ttl")
    >>> registry = parser.parse()
    >>> print(f"解析了 {len(registry.types)} 个类型")
    
    >>> multi_parser = MultiOntologyParser(["ontology1.ttl", "ontology2.owl"])
    >>> merged_registry = multi_parser.parse_all()
    >>> print(f"合并后共 {len(merged_registry.types)} 个类型")
"""

import logging
import re
from typing import List, Optional

from rdflib import OWL, RDF, RDFS, Graph, URIRef

from app.core.memory.models.ontology_general_models import (
    GeneralOntologyType,
    GeneralOntologyTypeRegistry,
    OntologyFileFormat,
)

logger = logging.getLogger(__name__)


class OntologyParser:
    """统一本体文件解析器
    
    解析本体文件并提取类定义，构建类型注册表。支持多种 RDF 格式，
    并提供格式自动推断和回退机制。
    
    Attributes:
        file_path: 本体文件路径
        file_format: 文件格式，如果未指定则根据扩展名推断
        graph: rdflib Graph 实例，用于存储解析后的 RDF 数据
    
    Example:
        >>> parser = OntologyParser("dbpedia.owl")
        >>> registry = parser.parse()
        >>> person_type = registry.get_type("Person")
        >>> if person_type:
        ...     print(f"Person URI: {person_type.class_uri}")
    """
    
    def __init__(
        self,
        file_path: str,
        file_format: Optional[OntologyFileFormat] = None,
    ):
        """初始化解析器
        
        Args:
            file_path: 本体文件路径
            file_format: 文件格式，如果未指定则根据扩展名自动推断
        """
        self.file_path = file_path
        self.file_format = file_format or OntologyFileFormat.from_extension(file_path)
        self.graph = Graph()

    def parse(self) -> GeneralOntologyTypeRegistry:
        """解析本体文件，返回类型注册表
        
        首先尝试使用推断的格式解析文件，如果失败则尝试其他格式。
        解析成功后，遍历所有 owl:Class 和 rdfs:Class 定义，
        提取类信息并构建层次结构。
        
        Returns:
            GeneralOntologyTypeRegistry: 包含所有解析出的类型和层次结构的注册表
            
        Raises:
            ValueError: 当所有格式都无法解析文件时抛出
        """
        logger.info(f"开始解析本体文件: {self.file_path}")
        
        # 尝试解析，失败则尝试其他格式
        self._parse_with_fallback()
        
        registry = GeneralOntologyTypeRegistry()
        registry.source_files.append(self.file_path)
        
        # 遍历 owl:Class
        for class_uri in self.graph.subjects(RDF.type, OWL.Class):
            type_info = self._parse_class(class_uri)
            if type_info:
                registry.types[type_info.class_name] = type_info
                self._update_hierarchy(registry, type_info)
        
        # 遍历 rdfs:Class（避免重复）
        for class_uri in self.graph.subjects(RDF.type, RDFS.Class):
            uri_str = str(class_uri)
            # 检查是否已经作为 owl:Class 解析过
            if uri_str not in [t.class_uri for t in registry.types.values()]:
                type_info = self._parse_class(class_uri)
                if type_info and type_info.class_name not in registry.types:
                    registry.types[type_info.class_name] = type_info
                    self._update_hierarchy(registry, type_info)
        
        logger.info(f"本体解析完成: {len(registry.types)} 个类型")
        return registry
    
    def _parse_with_fallback(self) -> None:
        """尝试解析文件，失败时尝试其他格式
        
        首先使用推断的格式解析，如果失败则依次尝试 RDF_XML 和 TURTLE 格式。
        
        Raises:
            ValueError: 当所有格式都无法解析文件时抛出
        """
        try:
            self.graph.parse(self.file_path, format=self.file_format.value)
            return
        except Exception as e:
            logger.warning(f"使用 {self.file_format.value} 格式解析失败: {e}")
        
        # 尝试其他格式
        fallback_formats = [
            OntologyFileFormat.RDF_XML,
            OntologyFileFormat.TURTLE,
            OntologyFileFormat.N_TRIPLES,
            OntologyFileFormat.JSON_LD,
        ]
        
        for fmt in fallback_formats:
            if fmt != self.file_format:
                try:
                    self.graph.parse(self.file_path, format=fmt.value)
                    logger.info(f"使用回退格式 {fmt.value} 解析成功")
                    return
                except Exception:
                    continue
        
        raise ValueError(f"无法解析本体文件: {self.file_path}")
    
    def _update_hierarchy(
        self, 
        registry: GeneralOntologyTypeRegistry, 
        type_info: GeneralOntologyType
    ) -> None:
        """更新层次结构
        
        如果类型有父类，将其添加到层次结构中。
        
        Args:
            registry: 类型注册表
            type_info: 类型信息
        """
        if type_info.parent_class:
            if type_info.parent_class not in registry.hierarchy:
                registry.hierarchy[type_info.parent_class] = set()
            registry.hierarchy[type_info.parent_class].add(type_info.class_name)
    
    def _parse_class(self, class_uri: URIRef) -> Optional[GeneralOntologyType]:
        """解析单个类定义
        
        从 RDF 图中提取类的名称、URI、标签、描述和父类信息。
        过滤空白节点和内置类型（Thing、Resource）。
        
        Args:
            class_uri: 类的 URI 引用
            
        Returns:
            GeneralOntologyType 实例，如果应该跳过该类则返回 None
        """
        uri_str = str(class_uri)
        class_name = self._extract_local_name(uri_str)
        
        # 过滤空白节点和内置类型
        if not class_name:
            return None
        if class_name.startswith('_:'):
            return None
        if class_name in ('Thing', 'Resource'):
            return None
        # 过滤空白节点 URI（以 _: 开头或包含空白节点标识）
        if uri_str.startswith('_:'):
            return None
        
        # 提取标签
        labels = self._extract_labels(class_uri)
        
        # 提取描述
        description = self._extract_description(class_uri)
        
        # 提取父类
        parent_class = self._extract_parent_class(class_uri)
        
        return GeneralOntologyType(
            class_name=class_name,
            class_uri=uri_str,
            labels=labels,
            description=description,
            parent_class=parent_class,
            source_file=self.file_path
        )
    
    def _extract_labels(self, class_uri: URIRef) -> dict:
        """提取类的多语言标签
        
        从 rdfs:label 属性中提取所有语言的标签。
        如果没有标签，使用类名作为英文标签。
        
        Args:
            class_uri: 类的 URI 引用
            
        Returns:
            语言代码到标签文本的字典
        """
        labels = {}
        for label in self.graph.objects(class_uri, RDFS.label):
            lang = getattr(label, 'language', None) or "en"
            labels[lang] = str(label)
        
        # 如果没有标签，使用类名作为默认标签
        if not labels:
            class_name = self._extract_local_name(str(class_uri))
            if class_name:
                labels["en"] = class_name
        
        return labels
    
    def _extract_description(self, class_uri: URIRef) -> Optional[str]:
        """提取类的描述
        
        从 rdfs:comment 属性中提取描述，优先使用英文描述。
        
        Args:
            class_uri: 类的 URI 引用
            
        Returns:
            类的描述文本，如果没有则返回 None
        """
        description = None
        for comment in self.graph.objects(class_uri, RDFS.comment):
            lang = getattr(comment, 'language', None)
            # 优先使用英文描述
            if lang == "en":
                return str(comment)
            # 如果还没有描述，使用无语言标记或其他语言的描述
            if description is None:
                description = str(comment)
        return description
    
    def _extract_parent_class(self, class_uri: URIRef) -> Optional[str]:
        """提取类的父类
        
        从 rdfs:subClassOf 属性中提取第一个有效的父类。
        过滤内置类型（Thing、Resource）和空白节点。
        
        Args:
            class_uri: 类的 URI 引用
            
        Returns:
            父类名称，如果没有有效父类则返回 None
        """
        for parent_uri in self.graph.objects(class_uri, RDFS.subClassOf):
            parent_uri_str = str(parent_uri)
            # 跳过空白节点
            if parent_uri_str.startswith('_:'):
                continue
            
            parent_name = self._extract_local_name(parent_uri_str)
            # 过滤内置类型
            if parent_name and parent_name not in ('Thing', 'Resource'):
                return parent_name
        
        return None
    
    def _extract_local_name(self, uri: str) -> Optional[str]:
        """从 URI 中提取本地名称
        
        支持两种常见的 URI 格式：
        1. 使用 # 分隔的 URI，如 http://example.org/ontology#Person
        2. 使用 / 分隔的 URI，如 http://dbpedia.org/ontology/Person
        
        Args:
            uri: 完整的 URI 字符串
            
        Returns:
            本地名称，如果无法提取则返回 None
        """
        # 处理空白节点
        if uri.startswith('_:'):
            return None
        
        # 尝试使用 # 分隔
        if '#' in uri:
            local_name = uri.rsplit('#', 1)[1]
            if local_name:
                return local_name
        
        # 尝试使用 / 分隔
        if '/' in uri:
            local_name = uri.rsplit('/', 1)[1]
            if local_name:
                return local_name
        
        # 使用正则表达式作为最后手段
        match = re.search(r'[#/]([^#/]+)$', uri)
        return match.group(1) if match else None


class MultiOntologyParser:
    """多本体文件解析器
    
    支持加载多个本体文件并将它们合并到一个统一的类型注册表中。
    先加载的文件中的类型定义优先保留（当存在同名类型时）。
    
    Attributes:
        file_paths: 本体文件路径列表
    
    Example:
        >>> parser = MultiOntologyParser([
        ...     "General_purpose_entity.ttl",
        ...     "domain_specific.owl"
        ... ])
        >>> registry = parser.parse_all()
        >>> print(f"合并后共 {len(registry.types)} 个类型")
    """
    
    def __init__(self, file_paths: List[str]):
        """初始化多文件解析器
        
        Args:
            file_paths: 本体文件路径列表
        """
        self.file_paths = file_paths
    
    def parse_all(self) -> GeneralOntologyTypeRegistry:
        """解析所有本体文件并合并
        
        依次解析每个本体文件，并将结果合并到一个统一的注册表中。
        如果某个文件解析失败，会记录警告日志并跳过该文件继续处理。
        
        Returns:
            GeneralOntologyTypeRegistry: 合并后的类型注册表
        """
        merged_registry = GeneralOntologyTypeRegistry()
        
        for file_path in self.file_paths:
            try:
                parser = OntologyParser(file_path)
                registry = parser.parse()
                merged_registry.merge(registry)
                logger.info(f"已合并本体文件: {file_path}")
            except Exception as e:
                logger.warning(f"跳过无法解析的本体文件 {file_path}: {e}")
        
        logger.info(f"多本体合并完成: 共 {len(merged_registry.types)} 个类型")
        return merged_registry
