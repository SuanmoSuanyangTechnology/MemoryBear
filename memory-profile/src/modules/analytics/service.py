"""Profile analytics orchestration over narrow shared-store repositories."""

from __future__ import annotations

import asyncio
import json
import logging
import math
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar
from uuid import UUID

from src.common import redis_cache
from src.common.errors import BizCode, LocalizedProfileError
from src.common.normalization import (
    collapse_whitespace,
    datetime_to_epoch_ms,
    is_blank,
    parse_uuid,
    to_jsonable,
)
from src.i18n import EnumLabels, enum_labels
from src.modules.analytics.constants import (
    COMMUNITY_GRAPH_ENTITY_FIELDS,
    COMMUNITY_GRAPH_NODE_FIELDS,
    COUNT_KEYS,
    DEFAULT_PER_TYPE_LIMITS,
    ENTITY_CONTENT_GATED_FIELDS,
    MEMORY_TYPES,
    NODE_PROPERTY_WHITELIST,
    PREFERENCE_GROUP_EDGE_TYPE,
    PREFERENCE_GROUP_LABEL,
    PREFERENCE_GROUP_NODE_ID_PREFIX,
    SINGLE_TYPE_LIMIT_HARD_MAX,
    SUPPORTED_NODE_TYPES,
    TOTAL_NODES_CAP,
    WHITELIST_DEFAULT_FIELDS,
)
from src.modules.analytics.repository.end_user_profile import EndUserProfileRepository
from src.modules.analytics.repository.memory_graph import MemoryGraphRepository
from src.modules.analytics.repository.memory_statistics import MemoryStatisticsRepository
from src.modules.analytics.repository.workspace_statistics import (
    WorkspaceStatisticsCorruptError,
    WorkspaceStatisticsRepository,
    WorkspaceStatisticsUnavailableError,
)
from src.runtime import ProfileRuntime

logger = logging.getLogger(__name__)
T = TypeVar("T")


def _empty_memory_insight(message_key: str) -> dict[str, Any]:
    """记忆洞察空态：字段与成功响应同构。"""

    return {
        "memory_insight": None,
        "behavior_pattern": None,
        "key_findings": None,
        "actionable_findings": [],
        "growth_trajectory": None,
        "updated_at": None,
        "is_cached": False,
        "message_key": message_key,
    }


def _empty_user_summary(message_key: str) -> dict[str, Any]:
    """用户摘要空态：字段与成功响应同构。"""

    return {
        "user_summary": None,
        "personality": None,
        "core_values": None,
        "one_sentence": None,
        "tags": [],
        "updated_at": None,
        "is_cached": False,
        "message_key": message_key,
    }


def _zero_memory_statistics() -> list[dict[str, Any]]:
    return [{"type": memory_type, "count": 0, "percentage": 0.0} for memory_type in MEMORY_TYPES]


def _empty_graph(message_key: str) -> dict[str, Any]:
    return {
        "nodes": [],
        "edges": [],
        "statistics": {
            "total_nodes": 0,
            "total_edges": 0,
            "node_types": {},
            "edge_types": {},
            "per_type": {},
        },
        "message_key": message_key,
    }


def _empty_community_graph(message_key: str) -> dict[str, Any]:
    return {
        "nodes": [],
        "edges": [],
        "statistics": {
            "total_nodes": 0,
            "total_edges": 0,
            "node_types": {},
            "edge_types": {},
        },
        "message_key": message_key,
    }


def _shrink_type_limits(limits: dict[str, int], cap: int = TOTAL_NODES_CAP) -> dict[str, int]:
    if sum(limits.values()) <= cap:
        return limits
    weight_total = sum(DEFAULT_PER_TYPE_LIMITS.get(node_type, 0) for node_type in limits)
    if weight_total <= 0:
        weight_total = sum(limit > 0 for limit in limits.values())
        weights = {node_type: int(limit > 0) for node_type, limit in limits.items()}
    else:
        weights = {node_type: DEFAULT_PER_TYPE_LIMITS.get(node_type, 0) for node_type in limits}
    shares = {node_type: cap * weights[node_type] / weight_total for node_type in limits}
    shrunk = {
        node_type: min(math.floor(shares[node_type]), limits[node_type]) for node_type in limits
    }
    remaining = cap - sum(shrunk.values())
    order = sorted(limits, key=lambda node_type: (-(shares[node_type] % 1), node_type))
    while remaining > 0:
        eligible = [node_type for node_type in order if shrunk[node_type] < limits[node_type]]
        if not eligible:
            break
        for node_type in eligible:
            if remaining == 0:
                break
            shrunk[node_type] += 1
            remaining -= 1
    return shrunk


def _normalize_tags(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    tags: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        normalized = collapse_whitespace(item)
        key = normalized.casefold()
        if not normalized or len(normalized) > 12 or key in seen:
            continue
        seen.add(key)
        tags.append(normalized)
        if len(tags) == 5:
            break
    return tags


def _parse_findings(value: object) -> tuple[list[str], list[dict[str, str]]]:
    if value in (None, ""):
        return [], []
    parsed = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return [item.strip() for item in value.split("•") if item.strip()], []
    if not isinstance(parsed, list):
        return [], []
    if all(isinstance(item, str) for item in parsed):
        return [normalized for item in parsed if (normalized := collapse_whitespace(item))], []
    if not all(isinstance(item, dict) for item in parsed):
        return [], []
    findings: list[str] = []
    actionable: list[dict[str, str]] = []
    for item in parsed:
        finding_value = item.get("finding")
        action_value = item.get("recommended_action")
        if not isinstance(finding_value, str) or not isinstance(action_value, str):
            continue
        finding, action = collapse_whitespace(finding_value), collapse_whitespace(action_value)
        if finding and action:
            findings.append(finding)
            actionable.append({"finding": finding, "recommended_action": action})
    return findings, actionable


def _map_description(value: Any) -> Any:
    """``description`` 存的是分号分隔的字符串，读取时拆成数组（api 同款三分支）。"""

    if isinstance(value, str):
        return [item.strip() for item in value.replace("；", ";").split(";") if item.strip()]
    if isinstance(value, list):
        return value
    return []


def _properties(
    label: str, raw: dict[str, Any], rel_count: int, labels: EnumLabels
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in NODE_PROPERTY_WHITELIST.get(label, WHITELIST_DEFAULT_FIELDS):
        # 仓储层用 Cypher map projection 下推白名单，缺失属性会带上 null 值
        # （``properties(n)`` 则是直接不返回该键）。Neo4j 的属性不可能为 null，
        # 因此「值为 null」与「属性不存在」等价，统一按不存在处理。
        if key not in raw or raw[key] is None:
            continue
        value = raw[key]
        # 内容存在性判定基于清洗前的原始值，避免 to_jsonable 把空值包装成其它形态
        if label == "ExtractedEntity" and key in ENTITY_CONTENT_GATED_FIELDS:
            if is_blank(value):
                continue
        if key == "entity_type":
            value = labels.entity_type.get(value, "")
        elif key == "emotion_type":
            value = labels.emotion_type.get(value)
        elif key == "emotion_subject":
            value = labels.emotion_subject.get(value)
        elif key == "description":
            value = _map_description(value)
        result[key] = to_jsonable(value)
    result["associative_memory"] = rel_count
    return result


def _edge_item(row: dict[str, Any]) -> dict[str, Any]:
    """装配统一边条目（api ``_make_edge_item`` 的同字段口径）。

    ``predicate`` / ``predicate_surface`` / ``predicate_description`` 只有实体—实体
    语义关系（``EXTRACTED_RELATIONSHIP``）才有意义，其它边类型不输出这三个键；
    空值一律不写进条目。
    """

    props = row.get("properties") or {}
    rel_type = str(row["type"])
    item: dict[str, Any] = {
        "id": str(row["id"]),
        "type": rel_type,
        "created_at": to_jsonable(props.get("created_at")),
        "valid_at": to_jsonable(props.get("valid_at")),
    }
    if rel_type == "EXTRACTED_RELATIONSHIP":
        item["predicate"] = to_jsonable(props.get("predicate"))
        item["predicate_surface"] = to_jsonable(props.get("predicate_surface"))
        item["predicate_description"] = to_jsonable(props.get("predicate_description"))
    return {key: value for key, value in item.items() if value is not None}


def _unified_edges(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    extracted_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    standalone: list[dict[str, Any]] = []
    for row in rows:
        source, target = str(row["source"]), str(row["target"])
        if source == target:
            continue
        node_a, node_b = sorted((source, target))
        if row.get("type") == "EXTRACTED_RELATIONSHIP":
            extracted_groups[(node_a, node_b)].append(row)
        else:
            item = _edge_item(row)
            standalone.append(
                {
                    "node_a": node_a,
                    "node_b": node_b,
                    "total": 1,
                    "edge_type": "SINGLE",
                    "a_to_b": [item] if source == node_a else [],
                    "b_to_a": [item] if source == node_b else [],
                }
            )
    result: list[dict[str, Any]] = []
    for (node_a, node_b), group in sorted(extracted_groups.items()):
        a_to_b = [_edge_item(row) for row in group if str(row["source"]) == node_a]
        b_to_a = [_edge_item(row) for row in group if str(row["source"]) == node_b]
        if len(group) == 1:
            edge_type = "SINGLE"
        elif len(group) == 2 and a_to_b and b_to_a:
            edge_type = "BIDIRECTIONAL"
        elif a_to_b and b_to_a:
            edge_type = "MULTI_BIDIRECTIONAL"
        else:
            edge_type = "UNIDIRECTIONAL_MULTI"
        result.append(
            {
                "node_a": node_a,
                "node_b": node_b,
                "total": len(group),
                "edge_type": edge_type,
                "a_to_b": a_to_b,
                "b_to_a": b_to_a,
            }
        )
    return result + standalone


def _attach_preference_group(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    end_user_id: str,
    labels: EnumLabels,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """把真实 ``Preference`` 节点连到一个响应期虚拟分组节点（api 同名 helper）。

    返回新列表；真实节点的 ``associative_memory`` 只统计 Neo4j 关系，因此这里既不
    修改真实节点属性，也不把虚拟 ID 传给任何数据库查询。
    """

    preference_ids = [node["id"] for node in nodes if node.get("label") == "Preference"]
    if not preference_ids:
        return list(nodes), list(edges)

    group_id = f"{PREFERENCE_GROUP_NODE_ID_PREFIX}{end_user_id}"
    group_node = {
        "id": group_id,
        "label": PREFERENCE_GROUP_LABEL,
        "caption": PREFERENCE_GROUP_LABEL,
        "properties": {
            "name": labels.preference_group_name,
            "virtual": True,
            "preference_count": len(preference_ids),
        },
    }
    virtual_edges: list[dict[str, Any]] = []
    for preference_id in preference_ids:
        node_a, node_b = sorted((group_id, preference_id))
        item = {
            "id": f"virtual:has-preference:{preference_id}",
            "type": PREFERENCE_GROUP_EDGE_TYPE,
            "predicate": PREFERENCE_GROUP_EDGE_TYPE,
        }
        virtual_edges.append(
            {
                "node_a": node_a,
                "node_b": node_b,
                "total": 1,
                "edge_type": "SINGLE",
                "a_to_b": [item] if node_a == group_id else [],
                "b_to_a": [item] if node_b == group_id else [],
            }
        )

    return [*nodes, group_node], [*edges, *virtual_edges]


class ProfileAnalyticsService:
    def __init__(self, runtime: ProfileRuntime) -> None:
        self.end_user_profile = EndUserProfileRepository(runtime.postgres)
        self.memory_statistics = MemoryStatisticsRepository(runtime.postgres)
        self.memory_graph = MemoryGraphRepository(runtime.neo4j)
        self.workspace_statistics_repository = WorkspaceStatisticsRepository(runtime.redis)

    async def memory_insight(self, end_user_id: str, workspace_id: UUID) -> dict[str, Any]:
        """读缓存的记忆洞察；用户维度的问题一律走空态（文档 3.4 的 B 类响应）。"""

        user_id = parse_uuid(end_user_id)
        if user_id is None:
            return _empty_memory_insight("analytics.empty.invalid_insight_id")
        row = await self.end_user_profile.get_memory_insight(user_id, workspace_id)
        if row is None:
            return _empty_memory_insight("analytics.empty.user_not_found")
        findings, actionable = _parse_findings(row["key_findings"])
        cached = any(
            row[key]
            for key in ("memory_insight", "behavior_pattern", "key_findings", "growth_trajectory")
        )
        return {
            "memory_insight": row["memory_insight"],
            "behavior_pattern": row["behavior_pattern"],
            "key_findings": findings,
            "actionable_findings": actionable,
            "growth_trajectory": row["growth_trajectory"],
            "updated_at": datetime_to_epoch_ms(row["memory_insight_updated_at"]),
            "is_cached": cached,
        }

    async def user_summary(self, end_user_id: str, workspace_id: UUID) -> dict[str, Any]:
        """读缓存的用户摘要；只取摘要列，不捎带洞察那几列长文本。"""

        user_id = parse_uuid(end_user_id)
        if user_id is None:
            return _empty_user_summary("analytics.empty.invalid_user_id")
        row = await self.end_user_profile.get_user_summary(user_id, workspace_id)
        if row is None:
            return _empty_user_summary("analytics.empty.user_not_found")
        cached = any(
            row[key]
            for key in ("user_summary", "personality_traits", "core_values", "one_sentence_summary")
        )
        return {
            "user_summary": row["user_summary"],
            "personality": row["personality_traits"],
            "core_values": row["core_values"],
            "one_sentence": row["one_sentence_summary"],
            "tags": _normalize_tags(row["memory_tags"]),
            "updated_at": datetime_to_epoch_ms(row["user_summary_updated_at"]),
            "is_cached": cached,
        }

    @staticmethod
    async def _safe_count(name: str, call: Callable[[], Awaitable[int]]) -> int:
        try:
            return await call()
        except Exception:
            logger.warning("profile count degraded to zero: %s", name, exc_info=True)
            return 0

    async def node_statistics(self, end_user_id: str, workspace_id: UUID) -> list[dict[str, Any]]:
        # 只做存在性判定，不把画像长文本列载入后丢掉（api ``end_user_exists_async``
        # 同款取舍）；非法 ID 与用户不存在一律空态全零（文档 3.4 的 B 类响应）。
        user_id = parse_uuid(end_user_id)
        if user_id is None or not await self.end_user_profile.end_user_exists(
            user_id, workspace_id
        ):
            return _zero_memory_statistics()
        # PG 侧 6 个计数器合并为一条查询（每项真实 DB 工作 <1ms，开销在会话/连接上），
        # Neo4j 侧 4 个计数各自约一次往返（RTT 支配）保持并发。两组互不依赖，一起发。
        source_counts, neo_counts = await asyncio.gather(
            self._memory_type_source_counts(user_id),
            asyncio.gather(
                self._safe_count(
                    "valid summaries",
                    lambda: self.memory_graph.valid_summary_count(end_user_id),
                ),
                self._safe_count(
                    "episodic", lambda: self.memory_graph.episodic_count(end_user_id)
                ),
                self._safe_count(
                    "semantic explicit",
                    lambda: self.memory_graph.semantic_explicit_count(end_user_id),
                ),
                self._safe_count(
                    "emotional", lambda: self.memory_graph.emotional_count(end_user_id)
                ),
            ),
        )
        summaries, episodic, semantic, emotional = neo_counts
        counts = {
            "PERCEPTUAL_MEMORY": source_counts["perceptual"],
            "WORKING_MEMORY": (
                source_counts["active_conversations"] + source_counts["direct_sources"]
            ),
            "SHORT_TERM_MEMORY": (
                source_counts["short_term"] + source_counts["pending_conversations"]
            ),
            "EXPLICIT_MEMORY": episodic + semantic,
            "IMPLICIT_MEMORY": summaries if summaries >= 5 else 0,
            "EMOTIONAL_MEMORY": emotional,
            "EPISODIC_MEMORY": episodic,
            "FORGET_MEMORY": source_counts["forgotten"],
        }
        total = sum(counts.values())
        return [
            {
                "type": key,
                "count": counts[key],
                "percentage": round(counts[key] / total * 100, 2) if total else 0.0,
            }
            for key in MEMORY_TYPES
        ]

    async def _memory_type_source_counts(self, user_id: UUID) -> dict[str, int]:
        """PG 侧的 6 个计数器：优先一条查询取回（单连接、单快照）。

        合并查询失败时回落到逐指标查询，从而保留原先「单个指标失败只把该项降级为 0」
        的语义（见 :meth:`_safe_count`）；这也是仓储层保留那 6 个单指标方法的原因。
        """
        try:
            return await self.memory_statistics.memory_type_counts(user_id)
        except Exception:
            logger.warning(
                "memory type counts merged query degraded to per-metric queries",
                exc_info=True,
            )
        values = await asyncio.gather(
            self._safe_count(
                "perceptual", lambda: self.memory_statistics.perceptual_count(user_id)
            ),
            self._safe_count(
                "active conversations",
                lambda: self.memory_statistics.active_conversation_count(user_id),
            ),
            self._safe_count(
                "direct sources", lambda: self.memory_statistics.direct_source_count(user_id)
            ),
            self._safe_count(
                "short term", lambda: self.memory_statistics.short_term_count(user_id)
            ),
            self._safe_count(
                "pending conversations",
                lambda: self.memory_statistics.pending_conversation_count(user_id),
            ),
            self._safe_count(
                "forgotten", lambda: self.memory_statistics.forgotten_count(user_id)
            ),
        )
        return dict(zip(COUNT_KEYS, values, strict=True))

    @redis_cache(
        prefix="memory_analytics_graph_data",
        skip_args=["self"],
        id_arg="end_user_id",
    )
    async def graph_data(
        self,
        end_user_id: str,
        workspace_id: UUID,
        node_types: list[str] | None,
        limit: int,
        depth: int,
        center_node_id: str | None,
        per_type_limits: dict[str, int] | None,
        language: str,
    ) -> dict[str, Any]:
        user_id = parse_uuid(end_user_id)
        if user_id is None:
            return _empty_graph("analytics.empty.invalid_user_id")
        resolved = await self.end_user_profile.resolve_graph_end_user(user_id, workspace_id)
        if resolved is None:
            return _empty_graph("analytics.empty.user_not_found")
        graph_end_user_id = str(resolved)
        if center_node_id and center_node_id.startswith(PREFERENCE_GROUP_NODE_ID_PREFIX):
            return _empty_graph("analytics.empty.invalid_center_node")
        # Filter_Mode 收敛到与 SUPPORTED_NODE_TYPES 的交集并去重排序（api
        # ``compute_stat_types`` 用 ``sorted(type_limits.keys())``）。
        requested = (
            sorted(SUPPORTED_NODE_TYPES)
            if not node_types
            else sorted(
                {node_type for node_type in node_types if node_type in SUPPORTED_NODE_TYPES}
            )
        )
        stat_types = requested if not center_node_id else sorted(SUPPORTED_NODE_TYPES)
        # per_type 的全量计数只依赖 (graph_end_user_id, stat_types)，与节点/边查询
        # 互不依赖，因此在这里提前发起、与后续阶段重叠执行（原先排在流程末尾，
        # 等价于串行等待）。_safe_count 内部吞掉异常并回落为 0，该 future 不会抛错。
        totals_future = asyncio.gather(
            *[
                self._safe_count(
                    f"graph total {node_type}",
                    lambda t=node_type: self.memory_graph.count_type(graph_end_user_id, t),
                )
                for node_type in stat_types
            ]
        )
        rows: list[dict[str, Any]]
        type_limits: dict[str, int] = {}
        if center_node_id:
            # 不过滤 label：Center_Mode 的邻居可能是 Community 等未列入
            # SUPPORTED_NODE_TYPES 的类型，api 同样原样返回（白名单外的属性只留 caption）。
            rows = await self.memory_graph.centered_nodes(
                graph_end_user_id, center_node_id, depth, limit
            )
        else:
            type_limits = {
                node_type: (per_type_limits or {}).get(
                    node_type,
                    DEFAULT_PER_TYPE_LIMITS.get(
                        node_type, min(limit, SINGLE_TYPE_LIMIT_HARD_MAX)
                    ),
                )
                for node_type in requested
            }
            type_limits = _shrink_type_limits(type_limits)
            rows = await self._nodes_for_type_limits(graph_end_user_id, type_limits)
        # Resolve the language once per request; the node loop below only does dict.get.
        labels = enum_labels(language)
        nodes: list[dict[str, Any]] = []
        node_counts: dict[str, int] = defaultdict(int)
        for row in rows:
            label = str(row["label"])
            props = _properties(
                label, row.get("properties") or {}, int(row.get("rel_count") or 0), labels
            )
            # 与 api ``_format_nodes`` 一致：caption 只回落到 label，不借用 name
            caption = str(props["caption"]) if "caption" in props else label
            nodes.append(
                {"id": str(row["id"]), "label": label, "properties": props, "caption": caption}
            )
            node_counts[label] += 1
        # Center_Mode 的「是否触顶」以真实节点数为准，虚拟分组节点不参与
        returned_total = len(nodes)
        raw_edges: list[dict[str, Any]] = []
        try:
            raw_edges = await self.memory_graph.edges(
                graph_end_user_id, [node["id"] for node in nodes]
            )
        except Exception:
            logger.warning("graph edge query degraded to empty", exc_info=True)
        edge_counts: dict[str, int] = defaultdict(int)
        for edge in raw_edges:
            edge_counts[str(edge["type"])] += 1
        edge_data = _unified_edges(raw_edges)
        # 虚拟偏好分组节点在真实边查询结束后才加入；它只存在于响应里，
        # 因此不计入 returned_total，也不参与任何 Cypher 参数。
        preference_count = node_counts.get("Preference", 0)
        nodes, edge_data = _attach_preference_group(
            nodes, edge_data, end_user_id=graph_end_user_id, labels=labels
        )
        if preference_count:
            node_counts[PREFERENCE_GROUP_LABEL] = 1
            edge_counts[PREFERENCE_GROUP_EDGE_TYPE] = (
                edge_counts.get(PREFERENCE_GROUP_EDGE_TYPE, 0) + preference_count
            )
        totals = await totals_future
        per_type = {}
        for node_type, total in zip(stat_types, totals, strict=True):
            effective_limit = limit if center_node_id else type_limits.get(node_type, 0)
            returned = node_counts.get(node_type, 0)
            per_type[node_type] = {
                "returned": returned,
                "total": total,
                "limit": effective_limit,
                "truncated": (
                    effective_limit > 0
                    and total > returned
                    and (not center_node_id or returned_total >= effective_limit)
                ),
            }
        return {
            "nodes": nodes,
            "edges": edge_data,
            "statistics": {
                "total_nodes": len(nodes),
                "total_edges": len(edge_data),
                "node_types": dict(node_counts),
                "edge_types": dict(edge_counts),
                "per_type": per_type,
            },
        }

    async def _nodes_for_type_limits(
        self, graph_end_user_id: str, type_limits: dict[str, int]
    ) -> list[dict[str, Any]]:
        """并发拉取各 Node_Type 的节点，并逐项复现原串行实现的顺序与截断语义。

        原实现逐类型 ``await``，共 N 次串行往返（N = 请求的类型数）；这里用
        ``asyncio.gather`` 并发，再把结果按同一顺序回放「累加 → 触顶 TOTAL_NODES_CAP 即截断
        并停止」的语义，因此 ``rows`` 的内容与顺序与串行版本完全一致。

        异常语义同样对齐：被截断点跳过的类型用 ``return_exceptions=True`` 收集后
        丢弃（串行版本根本不会发起该查询），截断点之前出现的异常照旧向上抛出。
        """

        ordered = [
            (node_type, limit) for node_type, limit in type_limits.items() if limit
        ]
        if not ordered:
            return []

        results = await asyncio.gather(
            *(
                self.memory_graph.nodes_by_type(graph_end_user_id, node_type, limit)
                for node_type, limit in ordered
            ),
            return_exceptions=True,
        )

        rows: list[dict[str, Any]] = []
        for (_node_type, _limit), part in zip(ordered, results, strict=True):
            if isinstance(part, BaseException):
                raise part
            rows.extend(part)
            if len(rows) >= TOTAL_NODES_CAP:
                rows = rows[:TOTAL_NODES_CAP]
                break
        return rows

    @redis_cache(
        prefix="memory_analytics_community_graph",
        skip_args=["self"],
        id_arg="end_user_id",
    )
    async def community_graph(self, end_user_id: str, workspace_id: UUID) -> dict[str, Any]:
        user_id = parse_uuid(end_user_id)
        if user_id is None:
            return _empty_community_graph("analytics.empty.invalid_user_id")
        resolved = await self.end_user_profile.resolve_graph_end_user(user_id, workspace_id)
        if resolved is None:
            return _empty_community_graph("analytics.empty.user_not_found")
        graph_end_user_id = str(resolved)

        rows = await self.memory_graph.community_graph_rows(graph_end_user_id)
        nodes: dict[str, dict[str, Any]] = {}
        edges: dict[str, dict[str, Any]] = {}
        memberships: dict[str, list[str]] = defaultdict(list)
        entity_fields = COMMUNITY_GRAPH_ENTITY_FIELDS

        for row in rows:
            community_id = row.get("c_id")
            entity_id = row.get("e_id")
            neighbor_id = row.get("e2_id")
            community_props = row.get("c_props") or {}

            if community_id and community_id not in nodes:
                # 仓储层下推了属性投影，缺失属性会带 null 值回来（`properties()`
                # 则是不返回该键），因此按「值非空」而非「键存在」过滤。
                props = {
                    key: to_jsonable(community_props.get(key))
                    for key in COMMUNITY_GRAPH_NODE_FIELDS
                    if community_props.get(key) is not None
                }
                nodes[community_id] = {
                    "id": community_id,
                    "label": "Community",
                    "properties": props,
                }

            if entity_id and entity_id not in nodes:
                raw = row.get("e_props") or {}
                props = {
                    key: to_jsonable(raw.get(key))
                    for key in entity_fields
                    if raw.get(key) is not None
                }
                props["community_name"] = to_jsonable(community_props.get("name")) or ""
                nodes[entity_id] = {
                    "id": entity_id,
                    "label": "ExtractedEntity",
                    "properties": props,
                }

            if neighbor_id and neighbor_id not in nodes:
                raw = row.get("e2_props") or {}
                props = {
                    key: to_jsonable(raw.get(key))
                    for key in entity_fields
                    if raw.get(key) is not None
                }
                props["community_name"] = ""
                nodes[neighbor_id] = {
                    "id": neighbor_id,
                    "label": "ExtractedEntity",
                    "properties": props,
                }

            membership_id = row.get("b_id")
            if membership_id and membership_id not in edges:
                edges[membership_id] = {
                    "id": membership_id,
                    "source": entity_id,
                    "target": community_id,
                }
            if community_id and entity_id:
                members = memberships[community_id]
                if entity_id not in members:
                    members.append(entity_id)

            relation_id = row.get("r_id")
            if relation_id and relation_id not in edges and neighbor_id:
                relation_props = {
                    key: to_jsonable(value) for key, value in (row.get("r_props") or {}).items()
                }
                from_entity = bool(row.get("r_from_e"))
                edges[relation_id] = {
                    "id": relation_id,
                    "source": entity_id if from_entity else neighbor_id,
                    "target": neighbor_id if from_entity else entity_id,
                    "properties": relation_props,
                    "predicate_description": relation_props.get("predicate_description"),
                }

        for community_id, member_ids in memberships.items():
            community = nodes.get(community_id)
            if community is None:
                continue
            community["properties"]["member_entity_ids"] = member_ids
            community_name = community["properties"].get("name") or ""
            for member_id in member_ids:
                entity = nodes.get(member_id)
                if entity and not entity["properties"].get("community_name"):
                    entity["properties"]["community_name"] = community_name

        type_counts: dict[str, int] = defaultdict(int)
        for node in nodes.values():
            type_counts[node["label"]] += 1
        return {
            "nodes": list(nodes.values()),
            "edges": list(edges.values()),
            "statistics": {
                "total_nodes": len(nodes),
                "total_edges": len(edges),
                "node_types": dict(type_counts),
            },
        }

    async def workspace_statistics(self, workspace_id: UUID) -> dict[str, Any]:
        try:
            snapshots = await self.workspace_statistics_repository.workspace_snapshots(workspace_id)
        except WorkspaceStatisticsUnavailableError as exc:
            raise LocalizedProfileError(
                BizCode.SERVICE_UNAVAILABLE,
                "analytics.workspace_statistics.unavailable",
                detail="workspace statistics cache unavailable",
                status_code=503,
            ) from exc
        except WorkspaceStatisticsCorruptError as exc:
            raise LocalizedProfileError(
                BizCode.SERVICE_UNAVAILABLE,
                "analytics.workspace_statistics.corrupt",
                detail="workspace statistics cache corrupt",
                status_code=503,
            ) from exc
        if not snapshots:
            raise LocalizedProfileError(
                BizCode.SERVICE_UNAVAILABLE,
                "analytics.workspace_statistics.not_ready",
                detail="workspace statistics snapshot unavailable",
                status_code=503,
            )
        today = snapshots[0]
        yesterday = (
            snapshots[1]
            if len(snapshots) > 1
            else {
                "statistics": [
                    {"type": key, "count": 0, "percentage": 0.0} for key in MEMORY_TYPES
                ],
                "total_count": 0,
                "total_users": 0,
                "generated_at": None,
            }
        )
        yesterday_counts = {item["type"]: item["count"] for item in yesterday["statistics"]}
        statistics = []
        for item in today["statistics"]:
            previous = yesterday_counts.get(item["type"], 0)
            statistics.append(
                {
                    **item,
                    "change": round((item["count"] - previous) / previous, 4) if previous else None,
                }
            )
        statistics.sort(key=lambda item: item["count"], reverse=True)
        yesterday_total = yesterday["total_count"]
        return {
            **today,
            "statistics": statistics,
            "total_count_change": round(
                (today["total_count"] - yesterday_total) / yesterday_total, 4
            )
            if yesterday_total
            else None,
            "update": today["total_count"] - yesterday_total,
        }
