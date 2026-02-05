# -*- coding: utf-8 -*-
"""本体解析工具模块

本模块提供本体文件解析功能，支持多种 RDF 格式的本体文件解析。

Modules:
    ontology_parser: 本体文件解析器
"""

from .ontology_parser import MultiOntologyParser, OntologyParser

__all__ = ["OntologyParser", "MultiOntologyParser"]
