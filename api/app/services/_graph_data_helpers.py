# -*- coding: UTF-8 -*-
"""图数据可视化接口的纯函数 helper。

本模块集中放置不依赖 Neo4j / 数据库的可单测纯逻辑，便于 controller 与 service
层共享。任何与 Cypher 直接交互的封装均不应放在此处。

Validates: Requirements 2.1, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 7.3, 8.2
"""
import logging
import math
from logging import Logger
from typing import Dict, Iterable, Optional

from app.core.memory.constants.graph_data_constants import (
    DEFAULT_PER_TYPE_LIMIT_MAP,
    SINGLE_TYPE_LIMIT_HARD_MAX,
    SUPPORTED_NODE_TYPES,
    TOTAL_NODES_CAP,
)


_LOGGER: Logger = logging.getLogger(__name__)


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
    if raw is None:
        return {}

    if raw.strip() == "":
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
      则原样返回（极端兜底，调用方在常规路径上不会触发）。

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

    weight_sum = sum(
        DEFAULT_PER_TYPE_LIMIT_MAP.get(t, 0) for t in limits
    )
    if weight_sum == 0:
        # 极端兜底：所有类型都不在 DEFAULT_PER_TYPE_LIMIT_MAP 中，无法按比例缩减。
        return limits

    floored: Dict[str, int] = {}
    fractions: Dict[str, float] = {}
    for t, user_value in limits.items():
        weight = DEFAULT_PER_TYPE_LIMIT_MAP.get(t, 0)
        share = cap * (weight / weight_sum)
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

    log.warning(
        "per_type_limits 合计 %d 超过 TOTAL_NODES_CAP=%d，已等比缩减为 %s",
        total,
        cap,
        floored,
    )
    return floored


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
from typing import Any, List

from app.repositories.neo4j.cypher_queries import (
    GRAPH_NODES_BY_TYPE_LIMITS,
    GRAPH_NODES_REL_COUNT_BATCH,
    GRAPH_NODES_TOTAL_COUNT_BY_TYPE,
)
from app.repositories.neo4j.neo4j_connector import Neo4jConnector


async def _query_nodes_by_type_limits(
    connector: Neo4jConnector,
    end_user_id: str,
    type_limits: Dict[str, int],
) -> List[Dict[str, Any]]:
    """按 ``{Node_Type: Per_Type_Limit}`` 检索节点（封装 Q1）。

    实现方式（必须为「单类型 + 静态 ``$limit``」循环）::

        for node_type, limit in type_limits.items():
            if limit <= 0:                  # 0 表示跳过该类型
                continue
            rows = await connector.execute_query(
                GRAPH_NODES_BY_TYPE_LIMITS,
                end_user_id=end_user_id,
                node_type=node_type,
                limit=int(limit),
            )

    背景：Neo4j 不允许 ``LIMIT`` 引用运行期变量（``Neo.ClientError.Statement.SyntaxError
    50N42``），因此不能用 ``UNWIND $type_limits AS spec ... LIMIT spec.limit`` 写法。
    单类型循环把 ``$limit`` 作为静态参数下发，对每个非零 limit 类型发起一次查询，
    调用次数 ``= 非零 limit 类型数``（最多 = ``len(SUPPORTED_NODE_TYPES)`` 个常量），
    与节点数 N 无关，仍符合 Requirement 6.4「总查询次数与 N 无关」的语义。

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
            GRAPH_NODES_BY_TYPE_LIMITS,
            end_user_id=end_user_id,
            node_type=node_type,
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

    rows = await connector.execute_query(
        GRAPH_NODES_TOTAL_COUNT_BY_TYPE,
        end_user_id=end_user_id,
        supported_types=list(supported_types),
    )

    result: Dict[str, int] = {}
    for row in rows:
        label = row.get("label")
        if not label:
            continue
        result[label] = int(row.get("total", 0) or 0)
    return result
