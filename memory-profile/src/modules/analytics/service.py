"""Profile analytics orchestration over narrow shared-store repositories."""

from __future__ import annotations

import asyncio
import json
import logging
import math
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, TypeVar
from uuid import UUID

from src.common.errors import BizCode, ProfileError
from src.modules.analytics.repository.end_user_profile import EndUserProfileRepository
from src.modules.analytics.repository.memory_graph import (
    DEFAULT_PER_TYPE_LIMITS,
    NODE_PROPERTY_WHITELIST,
    SUPPORTED_NODE_TYPES,
    WHITELIST_DEFAULT_FIELDS,
    MemoryGraphRepository,
)
from src.modules.analytics.repository.memory_statistics import (
    COUNT_KEYS,
    MemoryStatisticsRepository,
)
from src.modules.analytics.repository.workspace_statistics import (
    WorkspaceStatisticsCorruptError,
    WorkspaceStatisticsRepository,
    WorkspaceStatisticsUnavailableError,
)
from src.runtime import ProfileRuntime

logger = logging.getLogger(__name__)
T = TypeVar("T")
_MEMORY_TYPES = (
    "PERCEPTUAL_MEMORY",
    "WORKING_MEMORY",
    "SHORT_TERM_MEMORY",
    "EXPLICIT_MEMORY",
    "IMPLICIT_MEMORY",
    "EMOTIONAL_MEMORY",
    "EPISODIC_MEMORY",
    "FORGET_MEMORY",
)
_ENTITY_TYPE_NAMES = {
    "Person": "人物实体节点",
    "Organization": "组织实体节点",
    "ORG": "组织实体节点",
    "Location": "地点实体节点",
    "LOC": "地点实体节点",
    "Event": "事件实体节点",
    "Concept": "概念实体节点",
    "Time": "时间实体节点",
    "Position": "职位实体节点",
    "WorkRole": "职业实体节点",
    "System": "系统实体节点",
    "Policy": "政策实体节点",
    "HistoricalPeriod": "历史时期实体节点",
    "HistoricalState": "历史国家实体节点",
    "HistoricalEvent": "历史事件实体节点",
    "EconomicFactor": "经济因素实体节点",
    "Condition": "条件实体节点",
    "Numeric": "数值实体节点",
}
_EMOTION_TYPE_NAMES = {
    "joy": "愉快",
    "surprise": "惊喜",
    "sadness": "悲伤",
    "fear": "恐惧",
    "anger": "生气",
    "neutral": "中性",
}
_EMOTION_SUBJECT_NAMES = {"self": "自己", "other": "别人", "object": "事物对象"}


def _zero_memory_statistics() -> list[dict[str, Any]]:
    return [{"type": memory_type, "count": 0, "percentage": 0.0} for memory_type in _MEMORY_TYPES]


def _empty_graph(message: str) -> dict[str, Any]:
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
        "message": message,
    }


def _empty_community_graph(message: str) -> dict[str, Any]:
    return {
        "nodes": [],
        "edges": [],
        "statistics": {
            "total_nodes": 0,
            "total_edges": 0,
            "node_types": {},
            "edge_types": {},
        },
        "message": message,
    }


def _shrink_type_limits(limits: dict[str, int], cap: int = 2000) -> dict[str, int]:
    if sum(limits.values()) <= cap:
        return limits
    weight_total = sum(DEFAULT_PER_TYPE_LIMITS.get(node_type, 0) for node_type in limits)
    if weight_total <= 0:
        weight_total = sum(limit > 0 for limit in limits)
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


def _timestamp_ms(value: datetime | None) -> int | None:
    if value is None:
        return None
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return int(aware.timestamp() * 1000)


def _normalize_tags(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    tags: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        normalized = " ".join(item.split())
        key = normalized.casefold()
        if not normalized or len(normalized) > 12 or key in seen:
            continue
        seen.add(key)
        tags.append(normalized)
        if len(tags) == 5:
            break
    return tags


def _normalize_text(value: str) -> str:
    return " ".join(value.split())


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
        return [normalized for item in parsed if (normalized := _normalize_text(item))], []
    if not all(isinstance(item, dict) for item in parsed):
        return [], []
    findings: list[str] = []
    actionable: list[dict[str, str]] = []
    for item in parsed:
        finding_value = item.get("finding")
        action_value = item.get("recommended_action")
        if not isinstance(finding_value, str) or not isinstance(action_value, str):
            continue
        finding, action = _normalize_text(finding_value), _normalize_text(action_value)
        if finding and action:
            findings.append(finding)
            actionable.append({"finding": finding, "recommended_action": action})
    return findings, actionable


def _clean(value: Any) -> Any:
    if hasattr(value, "to_native"):
        value = value.to_native()
    if isinstance(value, datetime):
        aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        return aware.isoformat().replace("+00:00", "Z")
    if isinstance(value, dict):
        return {str(key): _clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _properties(label: str, raw: dict[str, Any], rel_count: int) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in NODE_PROPERTY_WHITELIST.get(label, WHITELIST_DEFAULT_FIELDS):
        # 仓储层用 Cypher map projection 下推白名单，缺失属性会带上 null 值
        # （``properties(n)`` 则是直接不返回该键）。Neo4j 的属性不可能为 null，
        # 因此「值为 null」与「属性不存在」等价，统一按不存在处理。
        if key not in raw or raw[key] is None:
            continue
        value = raw[key]
        if label == "ExtractedEntity" and key in {"description", "description_summary"}:
            if (isinstance(value, str) and not value.strip()) or value == []:
                continue
        if key == "entity_type":
            value = _ENTITY_TYPE_NAMES.get(value, "")
        elif key == "emotion_type":
            value = _EMOTION_TYPE_NAMES.get(value)
        elif key == "emotion_subject":
            value = _EMOTION_SUBJECT_NAMES.get(value)
        elif key == "description" and isinstance(value, str):
            value = [item.strip() for item in value.replace("；", ";").split(";") if item.strip()]
        result[key] = _clean(value)
    result["associative_memory"] = rel_count
    return result


def _edge_item(row: dict[str, Any]) -> dict[str, Any]:
    props = row.get("properties") or {}
    return {
        key: value
        for key, value in {
            "id": str(row["id"]),
            "type": str(row["type"]),
            "created_at": _clean(props.get("created_at")),
            "valid_at": _clean(props.get("valid_at")),
            "predicate": _clean(props.get("predicate")),
            "predicate_surface": _clean(props.get("predicate_surface")),
            "predicate_description": _clean(props.get("predicate_description")),
        }.items()
        if value is not None
    }


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


class ProfileAnalyticsService:
    def __init__(self, runtime: ProfileRuntime) -> None:
        self.end_user_profile = EndUserProfileRepository(runtime.postgres)
        self.memory_statistics = MemoryStatisticsRepository(runtime.postgres)
        self.memory_graph = MemoryGraphRepository(runtime.neo4j)
        self.workspace_statistics_repository = WorkspaceStatisticsRepository(runtime.redis)

    async def require_user(
        self, end_user_id: str, workspace_id: UUID
    ) -> tuple[UUID, dict[str, Any]]:
        try:
            user_id = UUID(end_user_id)
        except (TypeError, ValueError) as exc:
            raise ProfileError(
                BizCode.INVALID_PARAMETER, "无效的用户ID格式", detail="invalid end_user_id"
            ) from exc
        row = await self.end_user_profile.get_end_user(user_id, workspace_id)
        if row is None:
            raise ProfileError(
                BizCode.USER_NOT_FOUND,
                "用户不存在",
                detail="end_user does not exist in workspace",
                status_code=404,
            )
        return user_id, row

    async def require_graph_user(self, end_user_id: str, workspace_id: UUID) -> str:
        try:
            user_id = UUID(end_user_id)
        except (TypeError, ValueError) as exc:
            raise ProfileError(
                BizCode.INVALID_PARAMETER, "无效的用户ID格式", detail="invalid end_user_id"
            ) from exc
        resolved = await self.end_user_profile.resolve_graph_end_user(user_id, workspace_id)
        if resolved is None:
            raise ProfileError(
                BizCode.USER_NOT_FOUND,
                "用户不存在",
                detail="end_user does not exist in workspace",
                status_code=404,
            )
        return str(resolved)

    async def memory_insight(self, end_user_id: str, workspace_id: UUID) -> dict[str, Any]:
        try:
            _, row = await self.require_user(end_user_id, workspace_id)
        except ProfileError as exc:
            if exc.code not in {BizCode.INVALID_PARAMETER, BizCode.USER_NOT_FOUND}:
                raise
            return {
                "memory_insight": None,
                "behavior_pattern": None,
                "key_findings": None,
                "actionable_findings": [],
                "growth_trajectory": None,
                "updated_at": None,
                "is_cached": False,
                "message": exc.message,
            }
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
            "updated_at": _timestamp_ms(row["memory_insight_updated_at"]),
            "is_cached": cached,
        }

    async def user_summary(self, end_user_id: str, workspace_id: UUID) -> dict[str, Any]:
        try:
            _, row = await self.require_user(end_user_id, workspace_id)
        except ProfileError as exc:
            if exc.code not in {BizCode.INVALID_PARAMETER, BizCode.USER_NOT_FOUND}:
                raise
            return {
                "user_summary": None,
                "personality": None,
                "core_values": None,
                "one_sentence": None,
                "tags": [],
                "updated_at": None,
                "is_cached": False,
                "message": exc.message,
            }
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
            "updated_at": _timestamp_ms(row["user_summary_updated_at"]),
            "is_cached": cached,
        }

    async def _safe_count(self, name: str, call: Callable[[], Awaitable[int]]) -> int:
        try:
            return await call()
        except Exception:
            logger.warning("profile count degraded to zero: %s", name, exc_info=True)
            return 0

    async def node_statistics(self, end_user_id: str, workspace_id: UUID) -> list[dict[str, Any]]:
        try:
            user_id, _ = await self.require_user(end_user_id, workspace_id)
        except ProfileError as exc:
            if exc.code in {BizCode.INVALID_PARAMETER, BizCode.USER_NOT_FOUND}:
                return _zero_memory_statistics()
            raise
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
            for key in _MEMORY_TYPES
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

    async def graph_data(
        self,
        end_user_id: str,
        workspace_id: UUID,
        node_types: list[str] | None,
        limit: int,
        depth: int,
        center_node_id: str | None,
        per_type_limits: dict[str, int] | None,
    ) -> dict[str, Any]:
        try:
            graph_end_user_id = await self.require_graph_user(end_user_id, workspace_id)
        except ProfileError as exc:
            if exc.code in {BizCode.INVALID_PARAMETER, BizCode.USER_NOT_FOUND}:
                return _empty_graph(exc.message)
            raise
        requested = (
            sorted(SUPPORTED_NODE_TYPES)
            if not node_types
            else [node_type for node_type in node_types if node_type in SUPPORTED_NODE_TYPES]
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
        rows: list[dict[str, Any]] = []
        type_limits: dict[str, int] = {}
        if center_node_id:
            rows = await self.memory_graph.centered_nodes(
                graph_end_user_id, center_node_id, depth, limit
            )
            rows = [row for row in rows if row.get("label") in SUPPORTED_NODE_TYPES]
        else:
            type_limits = {
                node_type: (per_type_limits or {}).get(
                    node_type,
                    DEFAULT_PER_TYPE_LIMITS.get(node_type, min(limit, 500)),
                )
                for node_type in requested
            }
            type_limits = _shrink_type_limits(type_limits)
            rows = await self._nodes_for_type_limits(graph_end_user_id, type_limits)
        nodes: list[dict[str, Any]] = []
        node_counts: dict[str, int] = defaultdict(int)
        for row in rows:
            label = str(row["label"])
            props = _properties(label, row.get("properties") or {}, int(row.get("rel_count") or 0))
            caption = str(props.get("caption") or props.get("name") or label)
            nodes.append(
                {"id": str(row["id"]), "label": label, "properties": props, "caption": caption}
            )
            node_counts[label] += 1
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
                    and (not center_node_id or len(nodes) >= effective_limit)
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
        ``asyncio.gather`` 并发，再把结果按同一顺序回放「累加 → 触顶 2000 即截断
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
            if len(rows) >= 2000:
                rows = rows[:2000]
                break
        return rows

    async def community_graph(self, end_user_id: str, workspace_id: UUID) -> dict[str, Any]:
        try:
            graph_end_user_id = await self.require_graph_user(end_user_id, workspace_id)
        except ProfileError as exc:
            if exc.code in {BizCode.INVALID_PARAMETER, BizCode.USER_NOT_FOUND}:
                return _empty_community_graph(exc.message)
            raise

        rows = await self.memory_graph.community_graph_rows(graph_end_user_id)
        nodes: dict[str, dict[str, Any]] = {}
        edges: dict[str, dict[str, Any]] = {}
        memberships: dict[str, list[str]] = defaultdict(list)
        entity_fields = ("name", "end_user_id", "description", "created_at", "entity_type")

        for row in rows:
            community_id = row.get("c_id")
            entity_id = row.get("e_id")
            neighbor_id = row.get("e2_id")
            community_props = row.get("c_props") or {}

            if community_id and community_id not in nodes:
                props = {
                    key: _clean(community_props.get(key))
                    for key in NODE_PROPERTY_WHITELIST["Community"]
                    if key in community_props
                }
                nodes[community_id] = {
                    "id": community_id,
                    "label": "Community",
                    "properties": props,
                }

            if entity_id and entity_id not in nodes:
                raw = row.get("e_props") or {}
                props = {key: _clean(raw.get(key)) for key in entity_fields if key in raw}
                props["community_name"] = _clean(community_props.get("name")) or ""
                nodes[entity_id] = {
                    "id": entity_id,
                    "label": "ExtractedEntity",
                    "properties": props,
                }

            if neighbor_id and neighbor_id not in nodes:
                raw = row.get("e2_props") or {}
                props = {key: _clean(raw.get(key)) for key in entity_fields if key in raw}
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
                    key: _clean(value) for key, value in (row.get("r_props") or {}).items()
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
            raise ProfileError(
                BizCode.SERVICE_UNAVAILABLE,
                "工作空间记忆统计服务暂不可用",
                detail="workspace statistics cache unavailable",
                status_code=503,
            ) from exc
        except WorkspaceStatisticsCorruptError as exc:
            raise ProfileError(
                BizCode.SERVICE_UNAVAILABLE,
                "工作空间记忆统计数据异常",
                detail="workspace statistics cache corrupt",
                status_code=503,
            ) from exc
        if not snapshots:
            raise ProfileError(
                BizCode.SERVICE_UNAVAILABLE,
                "工作空间记忆统计尚未生成，请稍后重试",
                detail="workspace statistics snapshot unavailable",
                status_code=503,
            )
        today = snapshots[0]
        yesterday = (
            snapshots[1]
            if len(snapshots) > 1
            else {
                "statistics": [
                    {"type": key, "count": 0, "percentage": 0.0} for key in _MEMORY_TYPES
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
