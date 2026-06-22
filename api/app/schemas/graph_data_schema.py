"""GET /api/memory-storage/analytics/graph_data 响应模型。

所有模型均为 Pydantic v2，统一使用 ``ConfigDict(extra="ignore")`` 静默丢弃多余字段，
以保证后端在装配响应或前端在反序列化时都能向后兼容（Requirement 3.1 / 7.4）。
"""
from typing import Any, Dict, List, Optional, Literal

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# 边类型枚举
# ---------------------------------------------------------------------------

EdgeType = Literal["SINGLE", "UNIDIRECTIONAL_MULTI", "BIDIRECTIONAL", "MULTI_BIDIRECTIONAL"]


class GraphNode(BaseModel):
    """单个节点的展示形态。"""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(..., description="Neo4j elementId")
    label: str = Field(..., description="节点 label，对应 Supported_Node_Types 之一")
    properties: Dict[str, Any] = Field(
        default_factory=dict,
        description="按 NODE_PROPERTY_WHITELIST 过滤后的属性 + associative_memory 计数",
    )
    caption: str = Field(..., description="前端展示文案；优先取 properties.caption，否则取 label")


class PerTypeStat(BaseModel):
    """单一 Node_Type 的截断元数据（Requirement 3）。"""

    model_config = ConfigDict(extra="ignore")

    returned: int = Field(..., ge=0, description="本次响应中该类型的节点数量")
    total: int = Field(..., ge=0, description="end_user 下该类型的全量节点总数")
    limit: int = Field(..., ge=0, description="本次请求该类型实际生效的 Per_Type_Limit")
    truncated: bool = Field(
        ...,
        description=(
            "是否因 Per_Type_Limit 限制而截断：limit>0 且 total>returned 时为 True。"
            "limit==0（主动跳过该类型）时恒为 False。"
        ),
    )


class GraphStatistics(BaseModel):
    """统计字段。同时保留旧字段以维持向后兼容（Requirement 3.6 / 7.4）。"""

    model_config = ConfigDict(extra="ignore")

    total_nodes: int = Field(0, ge=0, description="本次响应中节点总数")
    total_edges: int = Field(0, ge=0, description="本次响应中边总数")
    node_types: Dict[str, int] = Field(
        default_factory=dict,
        description="兼容字段：返回数量按 Node_Type 聚合（不含 total）",
    )
    edge_types: Dict[str, int] = Field(
        default_factory=dict,
        description="兼容字段：返回数量按关系类型聚合",
    )
    per_type: Dict[str, PerTypeStat] = Field(
        default_factory=dict,
        description="新增：每种 Node_Type 的 returned/total/limit/truncated",
    )


class EdgeGroupItem(BaseModel):
    """a_to_b / b_to_a 中的单条边条目，携带边类型和关系描述。"""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(..., description="边 elementId")
    type: str = Field(..., description="关系类型")
    created_at: Optional[str] = Field(
        default=None, description="关系创建时间 (ISO 8601)"
    )
    valid_at: Optional[str] = Field(
        default=None, description="关系有效期 (ISO 8601)"
    )
    predicate: Optional[str] = Field(
        default=None, description="关系谓词"
    )
    predicate_surface: Optional[str] = Field(
        default=None, description="关系原文表述"
    )
    predicate_description: Optional[str] = Field(
        default=None, description="关系描述"
    )


class UnifiedEdge(BaseModel):
    """统一的边条目——合并了原 edges 和 edge_groups。

    所有边（无论单边还是重边聚合组）都使用此结构：

    - ``node_a`` / ``node_b`` 始终按 elementId 字典序排序，保证同一对节点对
      应的条目唯一。
    - 方向信息由 ``a_to_b``（node_a → node_b）和 ``b_to_a``（node_b → node_a）
      两个桶承载。
    - ``edge_type`` 枚举区分四种类型：
        * SINGLE：该对节点间仅 1 条边；
        * UNIDIRECTIONAL_MULTI：>=2 条边，所有边指向同一方向；
        * BIDIRECTIONAL：恰好 2 条边，双向各 1 条；
        * MULTI_BIDIRECTIONAL：>=3 条边，两个方向均有。

    自环（source == target）不会创建条目。
    """

    model_config = ConfigDict(extra="ignore")

    node_a: str = Field(..., description="按 elementId 字典序较小的端点")
    node_b: str = Field(..., description="按 elementId 字典序较大的端点")
    total: int = Field(..., ge=1, description="本组涵盖的边总数，最少 1")
    edge_type: EdgeType = Field(
        ...,
        description="边类型：SINGLE | UNIDIRECTIONAL_MULTI | BIDIRECTIONAL | MULTI_BIDIRECTIONAL",
    )
    a_to_b: List[EdgeGroupItem] = Field(
        default_factory=list,
        description="source=node_a, target=node_b 的边列表",
    )
    b_to_a: List[EdgeGroupItem] = Field(
        default_factory=list,
        description="source=node_b, target=node_a 的边列表",
    )


class GraphDataResponse(BaseModel):
    """``analytics_graph_data`` 的返回结构。controller 仍包一层 ApiResponse。"""

    model_config = ConfigDict(extra="ignore")

    nodes: List[GraphNode] = Field(default_factory=list)
    edges: List[UnifiedEdge] = Field(
        default_factory=list,
        description="统一的边列表。所有边（单边和重边聚合组）均以此结构表示，通过 edge_type 区分类型。",
    )
    statistics: GraphStatistics = Field(
        default_factory=GraphStatistics,
        description="节点/边统计信息，包含旧字段与新增 per_type",
    )
    message: Optional[str] = Field(
        default=None,
        description="仅在用户不存在 / 参数无效等空结果场景出现，与现有行为兼容",
    )
