"""GET /api/memory-storage/analytics/graph_data 响应模型。

所有模型均为 Pydantic v2，统一使用 ``ConfigDict(extra="ignore")`` 静默丢弃多余字段，
以保证后端在装配响应或前端在反序列化时都能向后兼容（Requirement 3.1 / 7.4）。
"""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


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


class GraphEdge(BaseModel):
    """单条边的展示形态。两端节点必定都在响应 nodes 数组中。"""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(..., description="Neo4j 关系 elementId")
    source: str = Field(..., description="起点节点 elementId")
    target: str = Field(..., description="终点节点 elementId")
    type: str = Field(..., description="关系类型（type(r)）")
    properties: Dict[str, Any] = Field(default_factory=dict, description="关系属性")
    caption: str = Field(..., description="前端展示文案，缺省时由 service 层填充关系类型")


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


class GraphEdgeGroup(BaseModel):
    """同一对节点之间的多边聚合（含双向）。

    仅当两个节点间存在 ``>= 2`` 条边（无论方向）时，才会出现在响应的
    ``edge_groups`` 数组中。``node_a`` / ``node_b`` 取两端 elementId 的字典序
    升序，保证同一对节点对应唯一的分组——不论 Cypher 返回顺序如何，前端
    都能用 ``(node_a, node_b)`` 作为稳定 key。

    自环（``source == target``）不会构成分组。
    """

    model_config = ConfigDict(extra="ignore")

    node_a: str = Field(..., description="按 elementId 字典序较小的端点")
    node_b: str = Field(..., description="按 elementId 字典序较大的端点")
    total: int = Field(..., ge=2, description="本组涵盖的边总数（双向合计），最少 2")
    a_to_b: List[str] = Field(
        default_factory=list,
        description="source=node_a, target=node_b 的边 id 列表；按 edges 中出现顺序",
    )
    b_to_a: List[str] = Field(
        default_factory=list,
        description="source=node_b, target=node_a 的边 id 列表；按 edges 中出现顺序",
    )


class GraphDataResponse(BaseModel):
    """``analytics_graph_data`` 的返回结构。controller 仍包一层 ApiResponse。"""

    model_config = ConfigDict(extra="ignore")

    nodes: List[GraphNode] = Field(default_factory=list)
    edges: List[GraphEdge] = Field(default_factory=list)
    edge_groups: List[GraphEdgeGroup] = Field(
        default_factory=list,
        description=(
            "同一对节点之间存在多条边（双向合计 >= 2）的聚合视图。"
            "便于前端在重边场景渲染聚合标签或多边弧线。"
        ),
    )
    statistics: GraphStatistics = Field(
        default_factory=GraphStatistics,
        description="节点/边统计信息，包含旧字段与新增 per_type",
    )
    message: Optional[str] = Field(
        default=None,
        description="仅在用户不存在 / 参数无效等空结果场景出现，与现有行为兼容",
    )
