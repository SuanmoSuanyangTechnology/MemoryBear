"""
数据处理模块

包含所有数据处理相关的工具函数，包括文本处理、时间处理和本体定义。
"""

# 从子模块导出常用函数和类，保持向后兼容
from .text_utils import (
    escape_lucene_query,
    extract_plain_query,
)
from .time_utils import (
    validate_date_format,
    normalize_date,
    normalize_date_safe,
    preprocess_date_string,
)
from .ontology import (
    PREDICATE_DEFINITIONS,
    LABEL_DEFINITIONS,
    Predicate,
    StatementType,
    TemporalInfo,
    RelevenceInfo,
)

__all__ = [
    # text_utils
    "escape_lucene_query",
    "extract_plain_query",
    # time_utils
    "validate_date_format",
    "normalize_date",
    "normalize_date_safe",
    "preprocess_date_string",
    # ontology
    "PREDICATE_DEFINITIONS",
    "LABEL_DEFINITIONS",
    "Predicate",
    "StatementType",
    "TemporalInfo",
    "RelevenceInfo",
]
