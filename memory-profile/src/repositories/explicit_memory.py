"""Read-only Neo4j repository for explicit-memory queries (T07–T10).

Query structure, parameters, filters and ordering match the monolith's
``MemoryExplicitService`` and its base-class emotion helper. Preserve existing
end-user/memory filters; do not add ownership checks, list caps or new fallback
policies as part of this execution-only migration.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from src.common.vocabulary import normalize_emotion_type_or_raw
from src.infrastructure.neo4j import Neo4jManager

logger = logging.getLogger(__name__)

#: Same mapping as the monolith service's inline ``type_mapping`` (T08 filter).
_EPISODIC_TYPE_CN: dict[str, str] = {
    "conversation": "对话",
    "project_work": "项目/工作",
    "learning": "学习",
    "decision": "决策",
    "important_event": "重要事件",
}


class ExplicitMemoryRepository:
    """Neo4j read access for the explicit-memory endpoints."""

    def __init__(self, neo4j: Neo4jManager) -> None:
        self.neo4j = neo4j

    # ── T07 / T09: full lists ────────────────────────────────────────────────

    async def episodic_overview_records(self, end_user_id: str) -> list[dict[str, Any]]:
        query = """
        MATCH (s:MemorySummary)
        WHERE s.end_user_id = $end_user_id
          AND s.delete_at IS NULL
        RETURN elementId(s) AS id,
               s.name AS title,
               s.content AS content,
               s.created_at AS created_at
        ORDER BY s.created_at DESC
        """
        return await self.neo4j.read_query(query, end_user_id=end_user_id)

    async def semantic_explicit_records(self, end_user_id: str) -> list[dict[str, Any]]:
        query = """
        MATCH (e:ExtractedEntity)
        WHERE e.end_user_id = $end_user_id
          AND e.is_explicit_memory = true
          AND e.delete_at IS NULL
        RETURN elementId(e) AS id,
               e.name AS name,
               e.entity_type AS entity_type,
               e.description AS core_definition
        ORDER BY e.name ASC
        """
        return await self.neo4j.read_query(query, end_user_id=end_user_id)

    # ── T08: paged + filtered list ───────────────────────────────────────────

    async def episodic_total(self, end_user_id: str) -> int:
        """Unfiltered total — monolith step 1 (not affected by filters)."""
        query = """
        MATCH (s:MemorySummary)
        WHERE s.end_user_id = $end_user_id
          AND s.delete_at IS NULL
        RETURN count(s) AS total
        """
        result = await self.neo4j.read_query(query, end_user_id=end_user_id)
        return result[0]["total"] if result else 0

    def episodic_where(
        self,
        end_user_id: str,
        start_date: int | None,
        end_date: int | None,
        episodic_type: str,
    ) -> tuple[str, dict[str, Any]]:
        """Mirror the monolith's WHERE-clause assembly, clause order included."""
        where_clauses = [
            "s.end_user_id = $end_user_id",
            "s.delete_at IS NULL",
        ]
        params: dict[str, Any] = {"end_user_id": end_user_id}

        if start_date is not None and end_date is not None:
            start_dt = datetime.fromtimestamp(start_date / 1000, tz=UTC)
            end_dt = datetime.fromtimestamp(end_date / 1000, tz=UTC)
            start_iso = start_dt.strftime("%Y-%m-%dT") + "00:00:00.000000"
            end_iso = end_dt.strftime("%Y-%m-%dT") + "23:59:59.999999"
            where_clauses.append(
                "datetime(s.created_at) >= datetime($start_iso) "
                "AND datetime(s.created_at) <= datetime($end_iso)"
            )
            params["start_iso"] = start_iso
            params["end_iso"] = end_iso

        if episodic_type != "all":
            chinese_type = _EPISODIC_TYPE_CN.get(episodic_type)
            if chinese_type:
                where_clauses.append(
                    "(s.memory_type = $episodic_type OR s.memory_type = $chinese_type)"
                )
                params["episodic_type"] = episodic_type
                params["chinese_type"] = chinese_type
            else:
                where_clauses.append("s.memory_type = $episodic_type")
                params["episodic_type"] = episodic_type

        return " AND ".join(where_clauses), params

    async def episodic_filtered_count(self, where_str: str, params: dict[str, Any]) -> int:
        query = f"""
        MATCH (s:MemorySummary)
        WHERE {where_str}
        RETURN count(s) AS total
        """
        result = await self.neo4j.read_query(query, **params)
        return result[0]["total"] if result else 0

    async def episodic_page_records(
        self,
        where_str: str,
        params: dict[str, Any],
        skip: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        query = f"""
        MATCH (s:MemorySummary)
        WHERE {where_str}
        RETURN elementId(s) AS id,
            s.name AS title,
            s.memory_type AS memory_type,
            s.content AS content,
            s.created_at AS created_at
        ORDER BY s.created_at DESC
        SKIP $skip LIMIT $limit
        """
        return await self.neo4j.read_query(query, **params, skip=skip, limit=limit)

    # ── T10: point lookups ───────────────────────────────────────────────────

    async def episodic_detail_record(
        self, end_user_id: str, memory_id: str
    ) -> dict[str, Any] | None:
        query = """
        MATCH (s:MemorySummary)
        WHERE elementId(s) = $memory_id AND s.end_user_id = $end_user_id
          AND s.delete_at IS NULL
        RETURN s.name AS title,
               s.content AS content,
               s.created_at AS created_at
        """
        result = await self.neo4j.read_query(
            query, memory_id=memory_id, end_user_id=end_user_id
        )
        return result[0] if result else None

    async def semantic_detail_record(
        self, end_user_id: str, memory_id: str
    ) -> dict[str, Any] | None:
        query = """
        MATCH (e:ExtractedEntity)
        WHERE elementId(e) = $memory_id
          AND e.end_user_id = $end_user_id
          AND e.is_explicit_memory = true
          AND e.delete_at IS NULL
        RETURN e.name AS name,
               e.description AS core_definition,
               e.example AS detailed_notes,
               e.created_at AS created_at
        """
        result = await self.neo4j.read_query(
            query, memory_id=memory_id, end_user_id=end_user_id
        )
        return result[0] if result else None

    async def episodic_emotion(self, end_user_id: str, summary_id: str) -> Any:
        """Monolith ``MemoryBaseService.extract_episodic_emotion``, verbatim."""
        query = """
        MATCH (s:MemorySummary)
        WHERE elementId(s) = $summary_id AND s.end_user_id = $end_user_id
          AND s.delete_at IS NULL
        MATCH (s)-[:DERIVED_FROM_STATEMENT]->(stmt:Statement)
        WHERE stmt.emotion_type IS NOT NULL
          AND stmt.emotion_intensity IS NOT NULL
        RETURN stmt.emotion_type AS emotion_type,
               stmt.emotion_intensity AS emotion_intensity
        ORDER BY emotion_intensity DESC
        LIMIT 1
        """
        try:
            result = await self.neo4j.read_query(
                query, summary_id=summary_id, end_user_id=end_user_id
            )
            if result:
                return normalize_emotion_type_or_raw(result[0].get("emotion_type"))
            return None
        except Exception:
            # Legacy detail reads still succeed when this optional lookup fails.
            logger.error("提取情景记忆情绪时出错", exc_info=True)
            return None
