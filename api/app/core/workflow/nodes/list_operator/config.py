from typing import Any
from pydantic import BaseModel, Field

from app.core.workflow.nodes.base_config import BaseNodeConfig
from app.core.workflow.nodes.enums import ComparisonOperator


class FilterCondition(BaseModel):
    key: str = ""
    comparison_operator: ComparisonOperator = ComparisonOperator.CONTAINS
    value: str | list[str] | bool = ""


class FilterBy(BaseModel):
    enabled: bool = False
    conditions: list[FilterCondition] = Field(default_factory=list)


class OrderByConfig(BaseModel):
    enabled: bool = False
    key: str = ""
    value: str = "asc"  # "asc" | "desc"


class Limit(BaseModel):
    enabled: bool = False
    size: int = -1


class ExtractConfig(BaseModel):
    enabled: bool = False
    serial: str = "1"  # 1-based index string, e.g. "1" = first


class ListOperatorNodeConfig(BaseNodeConfig):
    """
    List Operator node config.
    Operation order: filter -> extract -> order -> limit
    """
    input_list: str = Field(..., description="Variable selector, e.g. {{ sys.files }} or {{ conv.uploaded_files }}")
    filter_by: FilterBy = Field(default_factory=FilterBy)
    order_by: OrderByConfig = Field(default_factory=OrderByConfig)
    limit: Limit = Field(default_factory=Limit)
    extract_by: ExtractConfig = Field(default_factory=ExtractConfig)
