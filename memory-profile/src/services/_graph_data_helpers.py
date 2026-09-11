"""图数据装配的纯逻辑（不触碰 Neo4j，可脱离数据库单测）。

移植自老单体 ``api/app/services/_graph_data_helpers.py`` + ``user_memory_service.py``
的相关片段。分三组：

- **限额解析**：``resolve_per_type_limits`` / ``apply_total_cap_shrink`` /
  ``resolve_mode_and_type_limits`` / ``compute_stat_types``
- **统计装配**：``assemble_per_type_stat`` / ``assemble_center_per_type_stat``
- **节点/边整形**：``extract_node_properties`` / ``make_edge_item`` /
  ``classify_edge_type`` / ``build_unified_edges`` / ``resolve_edge_caption``

与老单体的差异：原文件里 ``_clean_neo4j_value`` 负责把 neo4j 时间类型转 ISO 字符串，
本服务已在 ``infrastructure/neo4j/client.py`` 统一转换（返回值恒为 JSON 安全类型），
故此处不再重复。
"""

import math
from collections.abc import Iterable
from typing import Any

from src.constants.display_mappings import (
    EMOTION_SUBJECT_MAPPING,
    EMOTION_TYPE_MAPPING,
    ENTITY_TYPE_MAPPING,
)
from src.constants.graph_data_constants import (
    _DEFAULT_FIELDS,
    DEFAULT_PER_TYPE_LIMIT_MAP,
    NODE_PROPERTY_WHITELIST,
    SINGLE_TYPE_LIMIT_HARD_MAX,
    SUPPORTED_NODE_TYPES,
    TOTAL_NODES_CAP,
)
from src.infrastructure.logger.config import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# 限额解析
# ---------------------------------------------------------------------------

def resolve_per_type_limits(
    target_types: Iterable[str],
    user_overrides: dict[str, int],
    fallback_default: int,
) -> dict[str, int]:
    """合并用户显式 per_type_limits 与内置默认值（优先级：用户 > 内置 > 兜底）。

    Args:
        target_types: 需要解析 limit 的类型集合。
        user_overrides: 调用方显式指定的 ``{类型: 上限}``。
        fallback_default: 兜底值（控制器的 ``limit`` 参数），并钳到
            ``SINGLE_TYPE_LIMIT_HARD_MAX``。

    Returns:
        ``{类型: 上限}``，键集合等于 ``target_types``。
    """
    final: dict[str, int] = {}
    for node_type in target_types:
        if node_type in user_overrides:
            final[node_type] = user_overrides[node_type]
        elif node_type in DEFAULT_PER_TYPE_LIMIT_MAP:
            final[node_type] = DEFAULT_PER_TYPE_LIMIT_MAP[node_type]
        else:
            final[node_type] = min(fallback_default, SINGLE_TYPE_LIMIT_HARD_MAX)
    return final


def apply_total_cap_shrink(
    limits: dict[str, int],
    cap: int = TOTAL_NODES_CAP,
) -> dict[str, int]:
    """各类型上限合计超过 ``cap`` 时，按内置默认值的比例等比缩减。

    - 合计 ≤ ``cap`` → 原样返回（返回入参本身，调用方视为只读）。
    - 否则以 ``DEFAULT_PER_TYPE_LIMIT_MAP`` 的默认值为权重等比分配：
      ``share_t = cap * weight_t / Σweight``，再取 ``min(floor(share), 原值)``
      防上溢；余数按「小数部分降序、类型字典序升序」逐个 +1，且每类型不超过原值。
    - 全部类型都无默认权重时退化为「非零 limit 类型权重相等」的均匀分布；
      全为 0 则原样返回。
    - 触发缩减时打 warning（老单体 Requirement 8.2）。
    """
    total = sum(limits.values())
    if total <= cap:
        return limits

    weights: dict[str, int] = {t: DEFAULT_PER_TYPE_LIMIT_MAP.get(t, 0) for t in limits}
    weight_sum = sum(weights.values())
    uniform_fallback = False
    if weight_sum == 0:
        weights = {t: (1 if v > 0 else 0) for t, v in limits.items()}
        weight_sum = sum(weights.values())
        uniform_fallback = True
        if weight_sum == 0:
            return limits

    floored: dict[str, int] = {}
    fractions: dict[str, float] = {}
    for node_type, user_value in limits.items():
        share = cap * (weights[node_type] / weight_sum)
        share_floor = math.floor(share)
        floored[node_type] = min(share_floor, user_value)
        fractions[node_type] = share - share_floor

    remaining = cap - sum(floored.values())
    if remaining > 0:
        for node_type, _frac in sorted(fractions.items(), key=lambda kv: (-kv[1], kv[0])):
            if remaining <= 0:
                break
            if floored[node_type] < limits[node_type]:
                floored[node_type] += 1
                remaining -= 1

    logger.warning(
        "per_type_limits 合计 %d 超过 TOTAL_NODES_CAP=%d，已%s缩减为 %s",
        total, cap, "按均匀分布" if uniform_fallback else "等比", floored,
    )
    return floored


def resolve_mode_and_type_limits(
    node_types: list[str] | None,
    limit: int,
    per_type_limits: dict[str, int] | None,
) -> tuple[str, dict[str, int]]:
    """Filter/Default 模式分派 + 每类型上限解析 + 总量上限缩减。

    Center_Mode 不走本函数（它由单一全局 ``limit`` 控制邻居总量）。

    Returns:
        ``(mode, type_limits)``，``mode`` ∈ ``{"Filter", "Default"}``。
    """
    if node_types:
        target_types = [t for t in node_types if t in SUPPORTED_NODE_TYPES]
        mode = "Filter"
    else:
        target_types = sorted(SUPPORTED_NODE_TYPES)
        mode = "Default"

    type_limits = resolve_per_type_limits(
        target_types=target_types,
        user_overrides=dict(per_type_limits or {}),
        fallback_default=limit,
    )
    return mode, apply_total_cap_shrink(type_limits)


def compute_stat_types(mode: str, type_limits: dict[str, int]) -> list[str]:
    """推导 ``statistics.per_type`` 应覆盖的类型集合。

    Filter_Mode 收敛到 ``type_limits`` 的键集合（用户显式关心的类型）；Default /
    Center 用全部 ``SUPPORTED_NODE_TYPES``，以呈现"全量 vs 当前"对照。
    """
    if mode == "Filter":
        return sorted(type_limits.keys())
    return sorted(SUPPORTED_NODE_TYPES)


# ---------------------------------------------------------------------------
# 统计装配
# ---------------------------------------------------------------------------

def assemble_per_type_stat(
    stat_types: Iterable[str],
    type_limits: dict[str, int],
    node_type_counts: dict[str, int],
    total_by_type: dict[str, int],
) -> dict[str, dict[str, Any]]:
    """装配 ``statistics.per_type`` 的 returned/total/limit/truncated。

    截断语义：``limit == 0`` 表示调用方主动跳过该类型，此时 ``returned=0`` 属预期，
    **不算截断**（否则客户端会把"主动跳过"误解为"后端容量截断"）。仅当
    ``limit > 0`` 且 ``total > returned`` 时才为 True。
    """
    per_type_stat: dict[str, dict[str, Any]] = {}
    for node_type in stat_types:
        returned = node_type_counts.get(node_type, 0)
        total = int(total_by_type.get(node_type, 0))
        type_limit = int(type_limits.get(node_type, 0))
        per_type_stat[node_type] = {
            "returned": returned,
            "total": total,
            "limit": type_limit,
            "truncated": type_limit > 0 and total > returned,
        }
    return per_type_stat


def assemble_center_per_type_stat(
    stat_types: Iterable[str],
    node_type_counts: dict[str, int],
    total_by_type: dict[str, int],
    *,
    global_limit: int,
    total_returned: int,
) -> dict[str, dict[str, Any]]:
    """Center_Mode 的 ``statistics.per_type``：截断是**全局**概念。

    Center_Mode 用一个全局 ``limit`` 控制邻居总量，不做按类型限流，故每类 ``limit``
    统一填全局值；``truncated`` 仅当「全局触顶（``total_returned >= global_limit``）
    且该类型 ``total > returned``」时为 True——既反映全局上限，又不给"该类型其实已
    全量返回"的类型误标截断。
    """
    limit_int = int(global_limit)
    global_truncated = limit_int > 0 and total_returned >= limit_int

    per_type_stat: dict[str, dict[str, Any]] = {}
    for node_type in stat_types:
        returned = node_type_counts.get(node_type, 0)
        total = int(total_by_type.get(node_type, 0))
        per_type_stat[node_type] = {
            "returned": returned,
            "total": total,
            "limit": limit_int,
            "truncated": global_truncated and total > returned,
        }
    return per_type_stat


# ---------------------------------------------------------------------------
# 节点整形
# ---------------------------------------------------------------------------

# ExtractedEntity 中需做"内容存在性"过滤的字段：空值不进响应
_ENTITY_CONTENT_GATED_FIELDS = frozenset({"description", "description_summary"})


def is_blank_content(value: Any) -> bool:
    """值是否视为「空内容」：None / 空串 / 纯空白串 / 空列表 / 全空白元素列表。"""
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, list):
        if not value:
            return True
        return all(isinstance(item, str) and item.strip() == "" for item in value)
    return False


def _map_description(value: Any) -> Any:
    """``description`` 写入侧是分号分隔字符串，响应时拆成数组。"""
    if isinstance(value, str):
        return [d.strip() for d in value.replace("；", ";").split(";") if d.strip()]
    if isinstance(value, list):
        return value
    return []


# 字段 → 展示映射。扩展新映射只需改本表，无需触碰 extract_node_properties。
_NODE_FIELD_VALUE_MAPPERS = {
    "entity_type": lambda v: ENTITY_TYPE_MAPPING.get(v, ""),
    "emotion_type": lambda v: EMOTION_TYPE_MAPPING.get(v),
    "emotion_subject": lambda v: EMOTION_SUBJECT_MAPPING.get(v),
    "description": _map_description,
}


def extract_node_properties(
    label: str,
    properties: dict[str, Any],
    *,
    rel_count: int,
) -> dict[str, Any]:
    """按 label 的白名单提取属性，并注入 ``associative_memory``（关联边数）。

    - 白名单外的字段一律丢弃（label 不在白名单时回落 ``_DEFAULT_FIELDS``）。
    - ``ExtractedEntity`` 的 ``description`` / ``description_summary`` 为**空内容**
      时不写入响应（判定基于清洗前的原始值）；节点本身始终返回。
    - ``rel_count`` 由调用方批量计数后注入（仓库层已合并 Q2，避免 N+1）。

    Returns:
        过滤后的属性字典，**至少含 ``associative_memory``**。
    """
    allowed_fields = NODE_PROPERTY_WHITELIST.get(label, _DEFAULT_FIELDS)

    filtered: dict[str, Any] = {}
    for field in allowed_fields:
        if field not in properties:
            continue
        value = properties[field]
        if label == "ExtractedEntity" and field in _ENTITY_CONTENT_GATED_FIELDS \
                and is_blank_content(value):
            continue
        mapper = _NODE_FIELD_VALUE_MAPPERS.get(field)
        if mapper is not None:
            value = mapper(value)
        filtered[field] = value

    filtered["associative_memory"] = rel_count
    return filtered


# ---------------------------------------------------------------------------
# 边整形
# ---------------------------------------------------------------------------

def resolve_edge_caption(rel_type: str | None, edge_props: dict[str, Any]) -> str | None:
    """派生边的展示文案：显式 caption → EXTRACTED_RELATIONSHIP 的 predicate → rel_type。"""
    explicit = edge_props.get("caption")
    if explicit:
        return explicit
    if rel_type == "EXTRACTED_RELATIONSHIP":
        return edge_props.get("predicate") or rel_type
    return rel_type


def make_edge_item(edge: dict[str, Any]) -> dict[str, Any]:
    """把一条已装配的边压成 UnifiedEdge 里的条目（仅保留展示所需字段）。"""
    edge_props = edge.get("properties") or {}
    rel_type = edge.get("type", "")

    item: dict[str, Any] = {"id": edge.get("id"), "type": rel_type}
    if edge_props.get("created_at") is not None:
        item["created_at"] = edge_props["created_at"]
    if edge_props.get("valid_at") is not None:
        item["valid_at"] = edge_props["valid_at"]
    if rel_type == "EXTRACTED_RELATIONSHIP":
        item["predicate"] = edge_props.get("predicate")
        item["predicate_surface"] = edge_props.get("predicate_surface")
        item["predicate_description"] = edge.get("predicate_description")
    return item


def classify_edge_type(a_to_b_count: int, b_to_a_count: int) -> str:
    """按双向桶中的边数判定边类型。"""
    total = a_to_b_count + b_to_a_count
    if total == 1:
        return "SINGLE"
    if total == 2 and a_to_b_count == 1 and b_to_a_count == 1:
        return "BIDIRECTIONAL"
    if a_to_b_count > 0 and b_to_a_count > 0:
        return "MULTI_BIDIRECTIONAL"
    return "UNIDIRECTIONAL_MULTI"


def build_unified_edges(edges: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """把边列表统一成 UnifiedEdge 结构。

    - ``EXTRACTED_RELATIONSHIP`` 按无向节点对 ``(node_a, node_b)`` 聚合（``node_a`` /
      ``node_b`` 按 elementId 字典序定序，保证同一对节点只产出一条），方向由
      ``a_to_b`` / ``b_to_a`` 两个桶承载，``edge_type`` 反映多重性。
    - 其他类型各自产出独立 ``SINGLE`` 条目。
    - 自环（``source == target``）与字段缺失的边跳过。

    Returns:
        每项 ``{node_a, node_b, total, edge_type, a_to_b, b_to_a}``。
    """
    relationship_groups: dict[tuple[str, str], dict[str, list[dict[str, Any]]]] = {}
    standalone_items: list[dict[str, Any]] = []

    for edge in edges:
        edge_id = edge.get("id")
        source = edge.get("source")
        target = edge.get("target")
        if not edge_id or not source or not target or source == target:
            continue

        edge_item = make_edge_item(edge)

        if source < target:
            node_a, node_b, direction = source, target, "a_to_b"
        else:
            node_a, node_b, direction = target, source, "b_to_a"

        if edge.get("type", "") == "EXTRACTED_RELATIONSHIP":
            bucket = relationship_groups.setdefault((node_a, node_b), {"a_to_b": [], "b_to_a": []})
            bucket[direction].append(edge_item)
        else:
            standalone_items.append({
                "node_a": node_a, "node_b": node_b,
                "edge_item": edge_item, "direction": direction,
            })

    result: list[dict[str, Any]] = []

    for (node_a, node_b), bucket in sorted(relationship_groups.items()):
        a_count, b_count = len(bucket["a_to_b"]), len(bucket["b_to_a"])
        result.append({
            "node_a": node_a,
            "node_b": node_b,
            "total": a_count + b_count,
            "edge_type": classify_edge_type(a_count, b_count),
            "a_to_b": bucket["a_to_b"],
            "b_to_a": bucket["b_to_a"],
        })

    for item in standalone_items:
        direction = item["direction"]
        result.append({
            "node_a": item["node_a"],
            "node_b": item["node_b"],
            "total": 1,
            "edge_type": "SINGLE",
            "a_to_b": [item["edge_item"]] if direction == "a_to_b" else [],
            "b_to_a": [item["edge_item"]] if direction == "b_to_a" else [],
        })

    return result


__all__ = [
    "apply_total_cap_shrink",
    "assemble_center_per_type_stat",
    "assemble_per_type_stat",
    "build_unified_edges",
    "classify_edge_type",
    "compute_stat_types",
    "extract_node_properties",
    "is_blank_content",
    "make_edge_item",
    "resolve_edge_caption",
    "resolve_mode_and_type_limits",
    "resolve_per_type_limits",
]