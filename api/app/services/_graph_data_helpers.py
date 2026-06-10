# -*- coding: UTF-8 -*-
"""图数据可视化接口的纯函数 helper。

本模块集中放置不依赖 Neo4j / 数据库的可单测纯逻辑，便于 controller 与 service
层共享。任何与 Cypher 直接交互的封装均不应放在此处。

Validates: Requirements 2.1, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 6.1, 6.3, 6.4,
            7.3, 8.2
"""
import logging
import math
from logging import Logger
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.core.memory.constants.graph_data_constants import (
    DEFAULT_PER_TYPE_LIMIT_MAP,
    SINGLE_TYPE_LIMIT_HARD_MAX,
    SUPPORTED_NODE_TYPES,
    TOTAL_NODES_CAP,
)
from app.repositories.neo4j.cypher_queries import (
    GRAPH_NODES_REL_COUNT_BATCH,
    build_graph_nodes_by_type_query,
    build_graph_total_count_by_type_query,
)
from app.repositories.neo4j.neo4j_connector import Neo4jConnector


_LOGGER: Logger = logging.getLogger(__name__)


# 受支持的「公开」接口。带下划线前缀的函数历史上已被同功能域的 service 与测试
# 直接引用；为明确表达它们是受支持的图数据编排接口，这里通过 ``__all__`` 公开，
# 并为跨模块稳定使用的函数提供无下划线别名（旧名保留以维持向后兼容）。
__all__ = [
    # 查询字符串解析 / 类型解析 / 总量缩减
    "parse_per_type_limits",
    "resolve_per_type_limits",
    "apply_total_cap_shrink",
    # 编排纯逻辑
    "resolve_mode_and_type_limits",
    "compute_stat_types",
    "assemble_per_type_stat",
    "assemble_center_per_type_stat",
    "build_edge_groups",
    # Neo4j 查询封装
    "query_nodes_by_type_limits",
    "query_rel_count_batch",
    "query_total_count_by_type",
]


def parse_per_type_limits(raw: Optional[str], logger: Logger) -> Dict[str, int]:
    """解析 ``per_type_limits`` 查询字符串为 ``{Type: int}`` 映射。

    输入示例: ``"Statement:100,MemorySummary:5"``。

    解析规则（严格遵循 design ``Algorithm 1``）:

    - ``raw`` 为 ``None`` 或仅含空白 → 返回 ``{}``。
    - 每个条目必须形如 ``Identifier:non-negative-int``：
        - 缺少冒号 → ``ValueError``。
        - ``Type`` 不是合法 Python identifier → ``ValueError``。
        - ``Limit`` 不是整数 → ``ValueError``。
        - ``Limit`` 为负数 → ``ValueError``。
    - ``Type`` 不在 :data:`SUPPORTED_NODE_TYPES` → 记录 warning 并跳过该条目。
    - ``Limit`` 超过 :data:`SINGLE_TYPE_LIMIT_HARD_MAX` → 截断并记录 warning。
    - 同一 ``Type`` 重复出现 → 记录 warning 并取最后一次值（与 querystring 习惯一致）。
    - ``Limit`` 等于 0 → 保留 0，调用方可据此跳过对应类型的查询（Requirement 2.9）。

    Args:
        raw: 原始查询字符串；可为 ``None`` 或空串。
        logger: 用于输出 warning 的 logger（由 controller 注入，便于上下文化）。

    Returns:
        合法且支持的 ``{Type: limit}`` 字典；若 ``raw`` 为空则为空字典。

    Raises:
        ValueError: 当 ``raw`` 中存在格式非法的条目（缺冒号 / 非整数 / 负数）时。
    """
    if not raw or not raw.strip():
        return {}

    result: Dict[str, int] = {}

    for entry in raw.split(","):
        entry = entry.strip()
        if entry == "":
            continue

        if ":" not in entry:
            raise ValueError(
                f"非法 per_type_limits 条目 '{entry}'，期望格式 'Type:Limit'"
            )

        type_part, limit_part = entry.split(":", 1)
        type_part = type_part.strip()
        limit_part = limit_part.strip()

        if not type_part.isidentifier():
            raise ValueError(f"非法 Node_Type '{type_part}'，必须为合法标识符")

        try:
            limit_val = int(limit_part)
        except ValueError as exc:
            raise ValueError(
                f"per_type_limits 中 {type_part} 的 limit 必须为整数: '{limit_part}'"
            ) from exc

        if limit_val < 0:
            raise ValueError(
                f"per_type_limits 中 {type_part} 的 limit 必须为非负整数: '{limit_part}'"
            )

        if type_part not in SUPPORTED_NODE_TYPES:
            logger.warning(
                "per_type_limits 包含未支持的 Node_Type，已忽略: %s",
                type_part,
            )
            continue

        if limit_val > SINGLE_TYPE_LIMIT_HARD_MAX:
            logger.warning(
                "per_type_limits 中 %s 的 limit=%d 超过单值上限 %d，已截断",
                type_part,
                limit_val,
                SINGLE_TYPE_LIMIT_HARD_MAX,
            )
            limit_val = SINGLE_TYPE_LIMIT_HARD_MAX

        if type_part in result:
            logger.warning(
                "per_type_limits 中 %s 重复出现，使用最后一次值 %d",
                type_part,
                limit_val,
            )

        result[type_part] = limit_val

    return result


def _resolve_per_type_limits(
    target_types: Iterable[str],
    user_overrides: Dict[str, int],
    fallback_default: int,
) -> Dict[str, int]:
    """合并用户显式 ``per_type_limits`` 与内置默认值，得到最终的 Per_Type_Limit 映射。

    严格遵循 design ``Algorithm 2`` 的优先级:

    1. 用户显式指定（``user_overrides[t]``）—— 最高优先级。
    2. 内置默认值（:data:`DEFAULT_PER_TYPE_LIMIT_MAP[t]`）。
    3. ``fallback_default``（来自 controller 透传的 ``limit`` 参数），并以
       :data:`SINGLE_TYPE_LIMIT_HARD_MAX` 钳制（Requirement 7.3）。

    Args:
        target_types: 本次需要解析 limit 的 Node_Type 集合。Default_Mode 下为
            :data:`SUPPORTED_NODE_TYPES`，Filter_Mode 下为 ``node_types`` 与
            :data:`SUPPORTED_NODE_TYPES` 的交集（由调用方先做过滤）。
        user_overrides: :func:`parse_per_type_limits` 的输出。键已经过
            :data:`SUPPORTED_NODE_TYPES` 过滤、值已被 :data:`SINGLE_TYPE_LIMIT_HARD_MAX`
            钳制；本函数不再重复校验。
        fallback_default: controller 透传的 ``limit`` 参数，作为「未在
            ``user_overrides`` 显式指定，且未在 :data:`DEFAULT_PER_TYPE_LIMIT_MAP`
            内置默认」时的兜底值。

    Returns:
        ``{Node_Type: Per_Type_Limit}`` 映射；键集合等于 ``target_types``。
    """
    final: Dict[str, int] = {}
    for t in target_types:
        if t in user_overrides:
            final[t] = user_overrides[t]
        elif t in DEFAULT_PER_TYPE_LIMIT_MAP:
            final[t] = DEFAULT_PER_TYPE_LIMIT_MAP[t]
        else:
            final[t] = min(fallback_default, SINGLE_TYPE_LIMIT_HARD_MAX)
    return final


def _apply_total_cap_shrink(
    limits: Dict[str, int],
    cap: int = TOTAL_NODES_CAP,
    logger: Optional[Logger] = None,
) -> Dict[str, int]:
    """当 ``limits`` 合计超过 ``cap`` 时，按默认值比例等比缩减各 Per_Type_Limit。

    严格遵循 design ``Algorithm 3``:

    - ``S = sum(limits.values())``。``S <= cap`` → 原样返回（不复制，与现有调用方约定一致；
      调用方需视为只读）。
    - 否则以 :data:`DEFAULT_PER_TYPE_LIMIT_MAP` 中各类型默认值的相对比例作为权重：
        - ``share_t = cap * default_weight_t / Σ default_weight``
        - ``floored_t = min(floor(share_t), 用户原值 limits[t])``——后者用于防止
          某类型用户原值小于按比例分配时上溢。
    - 余数 ``cap - Σ floored`` 按「分数部分降序、类型字典序升序」逐个 +1 分配，
      但每个类型不超过其用户原值（已达原值则跳过，保留确定性）。
    - 缩减发生时记录 warning 日志（Requirement 8.2）。
    - 类型不在 :data:`DEFAULT_PER_TYPE_LIMIT_MAP` 中时按权重 0 处理；若全部权重均为 0，
      则退化为「非零 limit 类型权重相等」的均匀分布（零 limit 类型不占用 cap），
      复用同一套等比缩减算法，确保 TOTAL_NODES_CAP 约束仍然生效；
      若全部 limit 均为 0，则原样返回。

    Args:
        limits: 经过 :func:`_resolve_per_type_limits` 解析后得到的初始 Per_Type_Limit
            映射；其中可能存在 0（``Limit==0`` 表示跳过查询）。
        cap: 总量上限，默认取 :data:`TOTAL_NODES_CAP`。
        logger: 可选的 logger；为 ``None`` 时使用模块 logger。便于上层注入带上下文
            的 logger 做日志聚合。

    Returns:
        缩减后的 ``{Node_Type: Per_Type_Limit}`` 映射；键集合等于 ``limits``。
        当未触发缩减时直接返回入参对象本身。
    """
    log = logger or _LOGGER

    total = sum(limits.values())
    if total <= cap:
        return limits

    # 计算各类型权重。常规路径直接取 DEFAULT_PER_TYPE_LIMIT_MAP 中的默认值；
    # 当所有类型都无默认权重（weight_sum == 0）时，退化为「非零 limit 类型权重=1」
    # 的均匀分布。均匀分布本质上是等比分布的特例，因此下方复用同一套缩减算法，
    # 避免逻辑重复并保持 tie-break 行为一致。
    weights: Dict[str, int] = {
        t: DEFAULT_PER_TYPE_LIMIT_MAP.get(t, 0) for t in limits
    }
    weight_sum = sum(weights.values())
    uniform_fallback = False
    if weight_sum == 0:
        # 极端兜底：所有类型都不在 DEFAULT_PER_TYPE_LIMIT_MAP 中。
        # 对非零 limit 类型赋予均匀权重（零 limit 表示跳过，不占用 cap）。
        weights = {t: (1 if v > 0 else 0) for t, v in limits.items()}
        weight_sum = sum(weights.values())
        uniform_fallback = True
        if weight_sum == 0:
            # 所有类型 limit 均为 0，无可分配份额，原样返回。
            return limits

    floored: Dict[str, int] = {}
    fractions: Dict[str, float] = {}
    for t, user_value in limits.items():
        share = cap * (weights[t] / weight_sum)
        share_floor = int(math.floor(share))
        # 防上溢：缩减后不应大于用户原值。
        floored[t] = min(share_floor, user_value)
        fractions[t] = share - share_floor

    remaining = cap - sum(floored.values())
    if remaining > 0:
        # 余数大的优先 +1；同余数按 type 字典序升序保证确定性。
        for t, _frac in sorted(
            fractions.items(),
            key=lambda kv: (-kv[1], kv[0]),
        ):
            if remaining <= 0:
                break
            if floored[t] < limits[t]:
                floored[t] += 1
                remaining -= 1

    if uniform_fallback:
        log.warning(
            "per_type_limits 合计 %d 超过 TOTAL_NODES_CAP=%d，"
            "且所有类型均无默认权重，已按均匀分布缩减为 %s",
            total,
            cap,
            floored,
        )
    else:
        log.warning(
            "per_type_limits 合计 %d 超过 TOTAL_NODES_CAP=%d，已等比缩减为 %s",
            total,
            cap,
            floored,
        )
    return floored


# ---------------------------------------------------------------------------
# 编排纯逻辑（模式分派 / 统计装配）—— 不触碰 Neo4j，便于单测
# ---------------------------------------------------------------------------
#
# 这几个函数从 service 层 ``analytics_graph_data`` 的编排流程中抽离出「不依赖
# Neo4j」的决策与装配逻辑，使其可脱离数据库直接单测：
#
# - :func:`resolve_mode_and_type_limits` —— Filter/Default 模式分派 + Per_Type_Limit
#   解析 + 总量上限缩减（不含 Center_Mode，后者直接走邻居查询无需类型解析）。
# - :func:`compute_stat_types` —— 依据模式推导 ``statistics.per_type`` 应覆盖的
#   Node_Type 集合。
# - :func:`assemble_per_type_stat` —— 依据 returned/total/limit 装配每类型的截断元数据。
#
# Validates: Requirements 1.3, 1.4, 2.9, 3.1, 3.7, 7.3, 8.2


def resolve_mode_and_type_limits(
    node_types: Optional[List[str]],
    limit: int,
    per_type_limits: Optional[Dict[str, int]],
) -> Tuple[str, Dict[str, int]]:
    """Filter/Default 模式分派 + Per_Type_Limit 解析 + 总量上限缩减（纯逻辑）。

    本函数不涉及 Center_Mode（中心节点查询不需要按类型解析 limit），仅处理
    ``analytics_graph_data`` 在非 Center 路径下的类型决策，便于脱离 Neo4j 单测。

    Args:
        node_types: 可选的 Node_Type 过滤列表。非空 → Filter_Mode（取与
            :data:`SUPPORTED_NODE_TYPES` 的交集）；空 → Default_Mode（覆盖全部
            :data:`SUPPORTED_NODE_TYPES`）。
        limit: 兜底 Per_Type_Limit，透传给 :func:`_resolve_per_type_limits`。
        per_type_limits: 用户显式指定的 ``{Node_Type: Per_Type_Limit}`` 映射，
            可为 ``None``。

    Returns:
        ``(mode, type_limits)`` —— ``mode`` ∈ ``{"Filter", "Default"}``；
        ``type_limits`` 为经 cap 缩减后的 ``{Node_Type: Per_Type_Limit}`` 映射。
    """
    if node_types:
        target_types = [t for t in node_types if t in SUPPORTED_NODE_TYPES]
        mode = "Filter"
    else:
        target_types = sorted(SUPPORTED_NODE_TYPES)
        mode = "Default"

    type_limits = _resolve_per_type_limits(
        target_types=target_types,
        user_overrides=dict(per_type_limits or {}),
        fallback_default=limit,
    )
    type_limits = _apply_total_cap_shrink(type_limits)
    return mode, type_limits


def compute_stat_types(mode: str, type_limits: Dict[str, int]) -> List[str]:
    """推导 ``statistics.per_type`` 应覆盖的 Node_Type 集合（纯逻辑）。

    - Filter_Mode 收敛到 ``type_limits`` 的键集合（用户显式关心的类型）。
    - 其余模式（Default / Center）使用全部 :data:`SUPPORTED_NODE_TYPES`，
      以呈现「全量 vs 当前」对照。

    Args:
        mode: ``{"Filter", "Default", "Center"}`` 之一。
        type_limits: 本次生效的 ``{Node_Type: Per_Type_Limit}`` 映射。

    Returns:
        排序后的 Node_Type 列表（字典序，保证确定性）。
    """
    if mode == "Filter":
        return sorted(type_limits.keys())
    return sorted(SUPPORTED_NODE_TYPES)


def assemble_per_type_stat(
    stat_types: Iterable[str],
    type_limits: Dict[str, int],
    node_type_counts: Dict[str, int],
    total_by_type: Dict[str, int],
) -> Dict[str, Dict[str, Any]]:
    """装配 ``statistics.per_type`` 的 returned/total/limit/truncated 元数据（纯逻辑）。

    截断语义：``limit==0`` 表示调用方主动跳过该类型的节点查询（Requirement 2.9），
    此时 ``returned`` 必为 0 属于预期行为，不应标记为「截断」；否则客户端会把
    「主动跳过」误解为「后端因容量限制截断」。仅当 ``limit>0`` 且 ``total>returned``
    时才认为发生了真正的截断。

    Args:
        stat_types: 需要装配的 Node_Type 集合（一般来自 :func:`compute_stat_types`）。
        type_limits: 本次生效的 ``{Node_Type: Per_Type_Limit}`` 映射。
        node_type_counts: 本次响应中按类型聚合的返回数量 ``{label: returned}``。
        total_by_type: Q4 全量计数结果 ``{label: total}``。

    Returns:
        ``{Node_Type: {returned, total, limit, truncated}}`` 映射。
    """
    per_type_stat: Dict[str, Dict[str, Any]] = {}
    for node_type in stat_types:
        returned = node_type_counts.get(node_type, 0)
        total = int(total_by_type.get(node_type, 0))
        type_limit = int(type_limits.get(node_type, 0))
        truncated = type_limit > 0 and total > returned
        per_type_stat[node_type] = {
            "returned": returned,
            "total": total,
            "limit": type_limit,
            "truncated": truncated,
        }
    return per_type_stat


def assemble_center_per_type_stat(
    stat_types: Iterable[str],
    node_type_counts: Dict[str, int],
    total_by_type: Dict[str, int],
    *,
    global_limit: int,
    total_returned: int,
) -> Dict[str, Dict[str, Any]]:
    """装配 Center_Mode 下的 ``statistics.per_type`` 元数据（纯逻辑）。

    Center_Mode 不做按类型限流，而是用一个**全局** ``limit`` 控制中心节点
    1..depth 跳邻居的**总**返回量。因此「截断」是全局概念，无法精确归因到
    某一类型：只要本次返回的节点总数达到了 ``global_limit``，就说明可能有更多
    邻居因全局上限被丢弃。

    本函数据此装配：

    - 每个类型的 ``limit`` 统一填全局 ``global_limit``（表达「本次受此上限约束」），
      而非 :func:`assemble_per_type_stat` 里那种逐类型独立的 Per_Type_Limit；
    - ``truncated``：仅当全局发生截断（``total_returned >= global_limit`` 且
      该类型 ``total > returned``）时才为 True——既反映全局上限触顶，又避免给
      「该类型其实已全量返回」的类型误标截断；
    - ``returned`` / ``total`` 语义与 :func:`assemble_per_type_stat` 一致。

    Args:
        stat_types: 需要装配的 Node_Type 集合（Center_Mode 下一般为全部
            :data:`SUPPORTED_NODE_TYPES`，以呈现「全量 vs 当前」对照）。
        node_type_counts: 本次响应中按类型聚合的返回数量 ``{label: returned}``。
        total_by_type: Q4 全量计数结果 ``{label: total}``。
        global_limit: Center_Mode 实际生效的全局节点上限（已被控制器钳制
            ≤ :data:`CENTER_MODE_LIMIT_HARD_MAX`）。
        total_returned: 本次响应中的节点总数（所有类型 returned 之和）。

    Returns:
        ``{Node_Type: {returned, total, limit, truncated}}`` 映射。
    """
    limit_int = int(global_limit)
    global_truncated = limit_int > 0 and int(total_returned) >= limit_int

    per_type_stat: Dict[str, Dict[str, Any]] = {}
    for node_type in stat_types:
        returned = node_type_counts.get(node_type, 0)
        total = int(total_by_type.get(node_type, 0))
        truncated = global_truncated and total > returned
        per_type_stat[node_type] = {
            "returned": returned,
            "total": total,
            "limit": limit_int,
            "truncated": truncated,
        }
    return per_type_stat


def build_edge_groups(
    edges: Iterable[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """聚合「同一对节点之间的多条边」并按方向分桶（纯逻辑）。

    业务背景：``EXTRACTED_RELATIONSHIP`` 等关系常常出现在同一对实体之间多次，
    例如 (A)-[:关联于 {predicate_surface: 女朋友}]->(B) 与
    (A)-[:关联于 {predicate_surface: 给…过生日}]->(B)。这些边在顶层 ``edges``
    数组中是平铺的；前端如果想呈现「重边」效果，需要自己做一次 O(N) 聚合。
    本函数把这部分逻辑下沉到后端，输出与方向无关的稳定分组。
    
    Args:
        edges: 已经装配好的响应边列表。每项至少含 ``id`` / ``source`` /
            ``target`` 三个字符串键；其它字段（type / properties / caption）
            本函数不读取，由调用方维护。

    Returns:
        ``edge_groups`` 字段对应的列表。每个元素形如::

            {
                "node_a": "A",
                "node_b": "B",
                "total": 3,
                "a_to_b": ["e1", "e3"],
                "b_to_a": ["e2"],
            }

        当不存在任何重边对时返回 ``[]``。
    """
    # 用 dict 收口，键 = (node_a, node_b) 元组（已字典序排序）。
    # 值同时记录两个方向的边 id 列表，避免二次遍历。
    groups: Dict[Tuple[str, str], Dict[str, List[str]]] = {}

    for edge in edges:
        edge_id = edge.get("id")
        source = edge.get("source")
        target = edge.get("target")
        if not edge_id or not source or not target:
            continue
        if source == target:
            # 自环不属于「双向重边」语义，跳过。
            continue

        if source < target:
            node_a, node_b = source, target
            direction = "a_to_b"
        else:
            node_a, node_b = target, source
            # source > target 时，原边实际是 b_to_a 方向。
            direction = "b_to_a"

        bucket = groups.get((node_a, node_b))
        if bucket is None:
            bucket = {"a_to_b": [], "b_to_a": []}
            groups[(node_a, node_b)] = bucket
        bucket[direction].append(edge_id)

    result: List[Dict[str, Any]] = []
    for (node_a, node_b), bucket in sorted(groups.items()):
        total = len(bucket["a_to_b"]) + len(bucket["b_to_a"])
        if total < 2:
            # 单边对不进 edge_groups；它已经在顶层 edges 中，前端无需再聚合。
            continue
        result.append({
            "node_a": node_a,
            "node_b": node_b,
            "total": total,
            "a_to_b": bucket["a_to_b"],
            "b_to_a": bucket["b_to_a"],
        })
    return result


# ---------------------------------------------------------------------------
# Neo4j 查询 helper（封装 graph_data 接口的 Q1/Q2/Q4）
# ---------------------------------------------------------------------------
#
# 三个 helper 把 Cypher 文本与结果整形从 service 主流程中抽离，便于：
# 1. 单元测试通过 mock ``Neo4jConnector.execute_query`` 验证传参；
# 2. 服务层主流程聚焦在「类型解析 → cap 缩减 → 结果装配」的业务逻辑；
# 3. 输入为空时短路返回空容器，避免向 Neo4j 发起无意义的查询（Requirement 6.3）。
#
# Validates: Requirements 6.1, 6.3, 6.4


async def _query_nodes_by_type_limits(
    connector: Neo4jConnector,
    end_user_id: str,
    type_limits: Dict[str, int],
) -> List[Dict[str, Any]]:
    """按 ``{Node_Type: Per_Type_Limit}`` 检索节点（封装 Q1）。

    实现方式（单类型循环 + label 字面量内联）::

        for node_type, limit in type_limits.items():
            if limit <= 0:                  # 0 表示跳过该类型
                continue
            rows = await connector.execute_query(
                build_graph_nodes_by_type_query(node_type),
                end_user_id=end_user_id,
                limit=int(limit),
            )

    两点背景：

    1. Neo4j 不允许 ``LIMIT`` 引用运行期变量（``Neo.ClientError.Statement.SyntaxError
       50N42``），因此不能用 ``UNWIND $type_limits AS spec ... LIMIT spec.limit`` 写法，
       只能对每个类型单独下发静态 ``$limit``。
    2. ``end_user_id`` 范围索引是 label-property 索引，规划器只有在 label 出现在
       MATCH 模式里（``MATCH (n:Statement)``）时才会走 NodeIndexSeek；把 label 放进
       WHERE 用 ``labels(n)[0] = $node_type``（或动态 label ``MATCH (n:$($node_type))``）
       都无法稳定命中索引、退化为 AllNodesScan。因此这里改用
       :func:`build_graph_nodes_by_type_query` 把白名单内的 label 字面量内联进模式。

    对每个非零 limit 类型发起一次查询，调用次数 ``= 非零 limit 类型数``（最多
    = ``len(SUPPORTED_NODE_TYPES)`` 个常量），与节点数 N 无关，符合 Requirement 6.4。

    Args:
        connector: 由调用方注入的 :class:`Neo4jConnector` 实例，便于在测试中 mock。
        end_user_id: 终端用户 UUID 字符串。
        type_limits: ``{Node_Type: Per_Type_Limit}`` 映射。``Per_Type_Limit==0`` 的
            条目会被本函数自动过滤（保留 0 会触发无意义的 ``LIMIT 0`` 查询）。

    Returns:
        合并后的 Cypher 行列表，每项包含 ``id`` / ``labels`` / ``properties`` 三个键。
        当 ``type_limits`` 为空、或所有值 ≤ 0 时直接返回 ``[]``，不发起查询
        （Requirement 6.3）。返回顺序为 ``type_limits`` 字典的迭代顺序（Python 3.7+
        保留插入顺序）。
    """
    if not type_limits:
        return []

    merged_rows: List[Dict[str, Any]] = []
    for node_type, limit in type_limits.items():
        limit_int = int(limit)
        if limit_int <= 0:
            continue
        rows = await connector.execute_query(
            build_graph_nodes_by_type_query(node_type),
            end_user_id=end_user_id,
            limit=limit_int,
        )
        if rows:
            merged_rows.extend(rows)

    return merged_rows


async def _query_rel_count_batch(
    connector: Neo4jConnector,
    node_ids: List[str],
) -> Dict[str, int]:
    """批量查询若干节点的关联边总数（封装 Q2，消除 N+1）。

    Args:
        connector: :class:`Neo4jConnector` 实例。
        node_ids: 节点 ``elementId`` 列表。

    Returns:
        ``{element_id: rel_count}`` 字典。当 ``node_ids`` 为空时返回 ``{}`` 且
        不发起查询（Requirement 6.3）。结果中缺失的节点会被调用方按 0 处理。
    """
    if not node_ids:
        return {}

    rows = await connector.execute_query(
        GRAPH_NODES_REL_COUNT_BATCH,
        node_ids=list(node_ids),
    )

    result: Dict[str, int] = {}
    for row in rows:
        node_id = row.get("id")
        if node_id is None:
            continue
        result[node_id] = int(row.get("rel_count", 0) or 0)
    return result


async def _query_total_count_by_type(
    connector: Neo4jConnector,
    end_user_id: str,
    supported_types: List[str],
) -> Dict[str, int]:
    """按 label 聚合 end_user 下的全量节点总数（封装 Q4）。

    与 Q1 同理：为命中 ``end_user_id`` 范围索引（label-property 索引），
    必须把 label 字面量内联进 MATCH 模式。旧版「``MATCH (n) WHERE labels(n)[0]
    IN $supported_types``」单查询缺少模式内 label，会对全库 AllNodesScan；
    这里改为对每个类型调用 :func:`build_graph_total_count_by_type_query`
    单独 NodeIndexSeek 计数，再合并为 ``{label: total}``。调用次数 = 类型数
    （最多 = ``len(SUPPORTED_NODE_TYPES)``，常数级），与节点数 N 无关。

    Args:
        connector: :class:`Neo4jConnector` 实例。
        end_user_id: 终端用户 UUID 字符串。
        supported_types: 需要计数的 Node_Type 列表（一般为
            :data:`SUPPORTED_NODE_TYPES`，或 Filter_Mode 下的过滤后类型）。

    Returns:
        ``{label: total}`` 字典。当 ``supported_types`` 为空时返回 ``{}`` 且
        不发起查询。结果中缺失的类型由调用方在装配 ``statistics.per_type``
        时按 0 处理。
    """
    if not supported_types:
        return {}

    result: Dict[str, int] = {}
    for node_type in supported_types:
        rows = await connector.execute_query(
            build_graph_total_count_by_type_query(node_type),
            end_user_id=end_user_id,
        )
        total = 0
        if rows:
            total = int(rows[0].get("total", 0) or 0)
        result[node_type] = total
    return result


# ---------------------------------------------------------------------------
# 无下划线公开别名
# ---------------------------------------------------------------------------
#
# 以下函数历史上以 ``_`` 前缀定义并被同功能域的 service/测试直接引用。为对外
# 明确表达「这是受支持的图数据编排接口」，提供无下划线别名；下划线旧名保留，
# 以维持既有 import 的向后兼容，避免一次性大规模重命名。
resolve_per_type_limits = _resolve_per_type_limits
apply_total_cap_shrink = _apply_total_cap_shrink
query_nodes_by_type_limits = _query_nodes_by_type_limits
query_rel_count_batch = _query_rel_count_batch
query_total_count_by_type = _query_total_count_by_type
