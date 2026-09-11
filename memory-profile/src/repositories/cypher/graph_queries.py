"""图数据（可视化）Cypher 语句集中处。

移植自老单体 ``api/app/repositories/neo4j/cypher_queries.py`` 的图数据部分
（Q1 取节点 / Q2 关系计数 / Q3 节点间边 / Q4 全量计数 / Center_Mode 邻居）。

## 为什么动态部分用「builder 内联」而不是查询参数

Cypher **不允许把 label（``MATCH (n:Label)``）作为参数** —— 只能字符串拼接。
拼接即注入面，故所有 builder 都先用 ``SUPPORTED_NODE_TYPES`` /
``NODE_PROPERTY_WHITELIST`` 白名单校验（不在白名单直接 ``ValueError``），
再内联。**新增 builder 必须照做**：凡是拼进 Cypher 的字符串都要先过白名单。

内联 label 还带来性能收益：``MATCH (n:Statement)`` 能命中 label-property 索引；
若写成 ``MATCH (n) WHERE labels(n)[0] IN $types`` 会对全库做 AllNodesScan。

## 字段投影

Q1 按类型投影 ``NODE_PROPERTY_WHITELIST``（而非 ``properties(n)`` 全量），避免把
``dialog_embedding`` 这类大字段拉过来。Center_Mode 返回**混合 label** 的节点集，
无法在查询前知道类型，故用 ``CASE labels(n)[0]`` 按 label 分别投影同一份白名单
（见 ``_build_label_case_projection``）。

## 参数约定

- ``$end_user_id`` (STRING)、``$limit`` (INTEGER)、``$node_ids`` (LIST<STRING>)、
  ``$center_node_id`` (STRING)。
- ``delete_at IS NULL`` 过滤软删节点；属性不存在时 ``IS NULL`` 为真，故老数据
  （无该字段）同样通过。
"""

from collections.abc import Iterable

from src.constants.graph_data_constants import (
    _DEFAULT_FIELDS,
    DEPTH_HARD_MAX,
    NODE_PROPERTY_WHITELIST,
    SUPPORTED_NODE_TYPES,
)


def _projection_entries(fields: Iterable[str], alias: str = "n") -> str:
    """把字段名拼成 Cypher map literal 的条目串（``k: n.k``）。

    字段名取自本模块常量（白名单），仍校验为合法标识符：常量写错（如
    ``"created at"``）时给明确错误，而不是让 Neo4j 报语法错。
    """
    entries = []
    for field in fields:
        if not field.isidentifier():
            raise ValueError(f"白名单字段名非法，拒绝拼进 Cypher: {field!r}")
        entries.append(f"{field}: {alias}.{field}")
    return ", ".join(entries)


def _build_label_case_projection(alias: str = "n") -> str:
    """生成 ``CASE labels(n)[0] WHEN '<label>' THEN {...} ... ELSE {...} END``。

    遍历 ``NODE_PROPERTY_WHITELIST`` 的**全部键**——包含 ``Community``：它不在
    ``SUPPORTED_NODE_TYPES`` 里（社区图谱走独立接口），但 Center_Mode 的邻居可能
    命中它，需要一并裁剪（见常量模块说明）。

    ``labels(n)[0]`` 的前提是单 label：实测线上 3610/3610 节点均为单 label。若将来
    出现多 label 节点导致取到非预期值，会落到 ``ELSE`` 只返回 ``_DEFAULT_FIELDS``
    （caption）——即**兜底只会少字段，不会把 embedding 漏出去**。
    """
    branches = []
    for label, fields in NODE_PROPERTY_WHITELIST.items():
        if not label.isidentifier():
            raise ValueError(f"白名单 label 非法，拒绝拼进 Cypher: {label!r}")
        entries = _projection_entries(fields, alias)
        branches.append(f"        WHEN '{label}' THEN {{{entries}}}")

    default_entries = _projection_entries(_DEFAULT_FIELDS, alias)
    branches.append(f"        ELSE {{{default_entries}}}")
    return "\n".join(branches)


def build_graph_nodes_by_type_query(node_type: str) -> str:
    """Q1：按单个 Node_Type 取节点（label 内联以命中索引，仅投影白名单字段）。

    Args:
        node_type: 节点 label，必须属于 ``SUPPORTED_NODE_TYPES``。

    Returns:
        Cypher；运行期参数 ``$end_user_id`` (STRING) 与 ``$limit`` (INTEGER)。

    Raises:
        ValueError: ``node_type`` 不在白名单内（拒绝内联进 Cypher）。
    """
    if node_type not in SUPPORTED_NODE_TYPES:
        raise ValueError(f"不支持的 Node_Type，拒绝内联进 Cypher: {node_type!r}")

    fields = NODE_PROPERTY_WHITELIST.get(node_type, _DEFAULT_FIELDS)
    # Neo4j 的 map literal {k: n.k} 会保留 null，缺字段的属性需在应用层过滤
    props_entries = _projection_entries(fields)

    return f"""
// GRAPH_NODES_BY_TYPE({node_type})
MATCH (n:{node_type})
WHERE n.end_user_id = $end_user_id
  AND n.delete_at IS NULL
RETURN
    elementId(n)        AS id,
    labels(n)           AS labels,
    {{{props_entries}}} AS properties
LIMIT $limit
"""


def build_graph_total_count_by_type_query(node_type: str) -> str:
    """Q4：单个 Node_Type 的全量计数（供 statistics.per_type 的 total 字段）。

    Args:
        node_type: 节点 label，必须属于 ``SUPPORTED_NODE_TYPES``。

    Returns:
        Cypher；运行期参数 ``$end_user_id`` (STRING)。

    Raises:
        ValueError: ``node_type`` 不在白名单内。
    """
    if node_type not in SUPPORTED_NODE_TYPES:
        raise ValueError(f"不支持的 Node_Type，拒绝内联进 Cypher: {node_type!r}")
    return f"""
// GRAPH_NODES_TOTAL_COUNT_BY_TYPE({node_type})
MATCH (n:{node_type})
WHERE n.end_user_id = $end_user_id
  AND n.delete_at IS NULL
RETURN count(n) AS total
"""


# Q2：批量取节点的关联边数，取代"每节点一次"的 N+1 子查询。
# 按 elementId 直接定位节点，不需要 label。
#
# 注意 Neo4j 5 起不能再写 ``size((n)--())``（pattern expression in size() 已移除），
# 必须用 ``COUNT { (n)--() }`` 子查询表达式。
# 参数：$node_ids (LIST<STRING>)
GRAPH_NODES_REL_COUNT_BATCH = """
// GRAPH_NODES_REL_COUNT_BATCH
UNWIND $node_ids AS nid
MATCH (n) WHERE elementId(n) = nid
  AND n.delete_at IS NULL
RETURN nid AS id, COUNT { (n)--() } AS rel_count
"""


# Q3：取给定节点集合**内部**的有向关系（两端的 id 都必须在集合里）。
# 参数：$node_ids (LIST<STRING>)
GRAPH_EDGES_AMONG_NODES = """
MATCH (n)-[r]->(m)
WHERE elementId(n) IN $node_ids
  AND elementId(m) IN $node_ids
RETURN
    elementId(r) as id,
    elementId(n) as source,
    elementId(m) as target,
    type(r) as rel_type,
    properties(r) as properties
"""


def build_center_node_neighbors_query(depth: int) -> str:
    """Center_Mode：以指定节点为中心取 1..depth 跳邻居（含中心节点本身）。

    邻居是**混合 label**，查询前不知道类型，故用 ``CASE labels(n)[0]`` 按 label
    分别投影白名单字段（见 ``_build_label_case_projection``）——避免老单体那样用
    ``properties(n)`` 把 ``dialog_embedding``/``summary_embedding`` 这类大字段
    整条拉回（实测单节点可带回多个 embedding）。

    软删过滤（``delete_at IS NULL``）在 collect **之后**统一施加，中心节点与邻居
    一视同仁：软删节点不出现在结果里（与 Q1 的语义一致）。注意路径仍可**穿过**
    软删节点连通两个活跃节点——本查询管的是"节点可见性"，不是"路径可达性"。
    若请求的中心节点本身已软删，则结果为它的活跃邻居（不含中心）。

    Args:
        depth: 跳数；此处再钳制一次到 ``[1, DEPTH_HARD_MAX]``（调用方可能漏钳，
            而 depth 会被内联进 Cypher，必须在此兜住）。

    Returns:
        Cypher；运行期参数 ``$end_user_id`` (STRING)、``$center_node_id`` (STRING)、
        ``$limit`` (INTEGER)。
    """
    safe_depth = max(1, min(int(depth), DEPTH_HARD_MAX))
    projection = _build_label_case_projection()
    return f"""
// GRAPH_CENTER_NODE_NEIGHBORS(depth={safe_depth})
MATCH path = (center)-[*1..{safe_depth}]-(connected)
WHERE center.end_user_id = $end_user_id
  AND elementId(center) = $center_node_id
WITH collect(DISTINCT center) + collect(DISTINCT connected) as all_nodes
UNWIND all_nodes as n
WITH DISTINCT n
WHERE n.delete_at IS NULL
RETURN
    elementId(n)    as id,
    labels(n)       as labels,
    CASE labels(n)[0]
{projection}
    END             as properties
LIMIT $limit
"""


__all__ = [
    "GRAPH_EDGES_AMONG_NODES",
    "GRAPH_NODES_REL_COUNT_BATCH",
    "build_center_node_neighbors_query",
    "build_graph_nodes_by_type_query",
    "build_graph_total_count_by_type_query",
]