"""Neo4j reads behind the insight and summary prompts."""

from __future__ import annotations

from src.domain.profile.read_models import EntityTag, StatementSample
from src.infrastructure.neo4j import Neo4jManager

#: api ``hot_memory_tags.get_raw_tags_from_db`` 的固定排除名单。
RAW_TAG_EXCLUDED_NAMES = [
    "AI",
    "Caroline",
    "Melanie",
    "Jon",
    "Gina",
    "用户",
    "AI助手",
    "John",
    "Maria",
]


class Neo4jProfileSourceReader:
    """Fallback statements, summary statements and raw entity tags."""

    def __init__(self, neo4j: Neo4jManager) -> None:
        self._neo4j = neo4j

    def recent_user_statements(
        self, end_user_id: str, statement_types: list[str], limit: int
    ) -> list[str]:
        """Insight fallback input: the user's most recent valid statements."""

        query = """
        MATCH (s:Statement)
        WHERE s.end_user_id = $end_user_id
          AND s.delete_at IS NULL
          AND s.speaker = "user"
          AND coalesce(s.has_unsolved_reference, false) = false
          AND s.stmt_type IN $statement_types
          AND s.statement IS NOT NULL
          AND trim(s.statement) <> ""
        RETURN s.statement AS statement
        ORDER BY s.dialog_at DESC, s.id ASC
        LIMIT $limit
        """
        rows = self._neo4j.execute_query(
            query,
            end_user_id=end_user_id,
            statement_types=statement_types,
            limit=limit,
        )
        return [row["statement"] for row in rows]

    def recent_statements(
        self, end_user_id: str, limit: int
    ) -> list[StatementSample]:
        """Summary input: statement bodies ordered by creation time."""

        query = (
            "MATCH (s:Statement) "
            "WHERE s.end_user_id = $end_user_id AND s.statement IS NOT NULL "
            "AND s.delete_at IS NULL "
            "RETURN s.statement AS statement, s.created_at AS created_at "
            "ORDER BY created_at DESC LIMIT $limit"
        )
        rows = self._neo4j.execute_query(query, end_user_id=end_user_id, limit=limit)
        return [
            StatementSample(
                statement=str(row.get("statement") or ""),
                created_at=row.get("created_at"),
            )
            for row in rows
        ]

    def raw_entity_tags(self, end_user_id: str, limit: int) -> list[EntityTag]:
        """Summary input: frequent entity names before LLM filtering."""

        query = (
            "MATCH (e:ExtractedEntity) "
            "WHERE e.end_user_id = $id AND e.entity_type <> '生命体' "
            "AND e.name IS NOT NULL AND NOT e.name IN $names_to_exclude "
            "RETURN e.name AS name, count(e) AS frequency "
            "ORDER BY frequency DESC "
            "LIMIT $limit"
        )
        rows = self._neo4j.execute_query(
            query,
            id=end_user_id,
            limit=limit,
            names_to_exclude=RAW_TAG_EXCLUDED_NAMES,
        )
        return [
            EntityTag(name=str(row["name"]), frequency=int(row["frequency"]))
            for row in rows
        ]
