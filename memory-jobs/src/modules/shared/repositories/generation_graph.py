"""Neo4j reads behind the insight, summary and interest inputs."""

from __future__ import annotations

from typing import Any

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

INTEREST_ENTITY_CANDIDATES = """
MATCH (s:Statement)-[:REFERENCES_ENTITY]->(e:ExtractedEntity)
WHERE s.end_user_id = $id
  AND e.end_user_id = $id
  AND s.delete_at IS NULL
  AND e.delete_at IS NULL
  AND e.id IS NOT NULL
  AND e.name IS NOT NULL
  AND (
    s.speaker IS NULL
    OR toLower(trim(toString(s.speaker))) = 'user'
  )
WITH e.id AS entity_id,
     trim(toString(e.name)) AS name,
     coalesce(toString(e.entity_type), '') AS entity_type,
     count(DISTINCT s.id) AS frequency
WHERE name <> ''
  AND NOT (toLower(name) IN $excluded_names)
  AND NOT name =~ $iso_datetime_pattern
  AND NOT name =~ $unix_timestamp_pattern
RETURN entity_id, name, entity_type, frequency
ORDER BY frequency DESC, toLower(name) ASC, entity_id ASC
LIMIT $limit
"""

INTEREST_STATEMENT_EVIDENCE = """
UNWIND range(0, size($entity_ids) - 1) AS entity_rank
WITH entity_rank, $entity_ids[entity_rank] AS entity_id
MATCH (s:Statement)-[:REFERENCES_ENTITY]->(e:ExtractedEntity {id: entity_id})
WHERE s.end_user_id = $id
  AND e.end_user_id = $id
  AND s.delete_at IS NULL
  AND e.delete_at IS NULL
  AND s.id IS NOT NULL
  AND s.statement IS NOT NULL
  AND trim(toString(s.statement)) <> ''
  AND (
    s.speaker IS NULL
    OR toLower(trim(toString(s.speaker))) = 'user'
  )
WITH entity_rank, entity_id, s
ORDER BY entity_rank ASC,
         CASE WHEN toLower(trim(toString(s.speaker))) = 'user' THEN 0 ELSE 1 END ASC,
         coalesce(s.dialog_at, s.created_at) DESC,
         s.id ASC
WITH entity_rank, entity_id, collect(s)[0..$per_entity_limit] AS statements
UNWIND statements AS s
WITH s, min(entity_rank) AS first_entity_rank
MATCH (s)-[:REFERENCES_ENTITY]->(related_entity:ExtractedEntity)
WHERE related_entity.id IN $entity_ids
  AND related_entity.end_user_id = $id
  AND related_entity.delete_at IS NULL
WITH s,
     first_entity_rank,
     collect(DISTINCT toString(related_entity.id)) AS related_entity_ids
RETURN toString(s.id) AS statement_id,
       [entity_id IN $entity_ids WHERE entity_id IN related_entity_ids] AS entity_ids,
       substring(trim(toString(s.statement)), 0, $max_chars) AS statement_text
ORDER BY first_entity_rank ASC,
         CASE WHEN toLower(trim(toString(s.speaker))) = 'user' THEN 0 ELSE 1 END ASC,
         coalesce(s.dialog_at, s.created_at) DESC,
         statement_id ASC
LIMIT $limit
"""


class GenerationGraphRepository:
    def __init__(self, neo4j: Neo4jManager) -> None:
        self.neo4j = neo4j

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
        rows = self.neo4j.execute_query(
            query,
            end_user_id=end_user_id,
            statement_types=statement_types,
            limit=limit,
        )
        return [row["statement"] for row in rows]

    def recent_statements(self, end_user_id: str, limit: int) -> list[dict[str, Any]]:
        """Summary input: statement bodies ordered by creation time."""

        query = (
            "MATCH (s:Statement) "
            "WHERE s.end_user_id = $end_user_id AND s.statement IS NOT NULL "
            "RETURN s.statement AS statement, s.created_at AS created_at "
            "ORDER BY created_at DESC LIMIT $limit"
        )
        rows = self.neo4j.execute_query(query, end_user_id=end_user_id, limit=limit)
        return [
            {"statement": row.get("statement", ""), "created_at": row.get("created_at")}
            for row in rows
        ]

    def raw_entity_tags(self, end_user_id: str, limit: int) -> list[tuple[str, int]]:
        """Summary input: frequent entity names before LLM filtering."""

        query = (
            "MATCH (e:ExtractedEntity) "
            "WHERE e.end_user_id = $id AND e.entity_type <> '生命体' "
            "AND e.name IS NOT NULL AND NOT e.name IN $names_to_exclude "
            "RETURN e.name AS name, count(e) AS frequency "
            "ORDER BY frequency DESC "
            "LIMIT $limit"
        )
        rows = self.neo4j.execute_query(
            query,
            id=end_user_id,
            limit=limit,
            names_to_exclude=RAW_TAG_EXCLUDED_NAMES,
        )
        return [(row["name"], row["frequency"]) for row in rows]

    def interest_candidates(
        self,
        end_user_id: str,
        *,
        limit: int,
        excluded_names: list[str],
        iso_datetime_pattern: str,
        unix_timestamp_pattern: str,
    ) -> list[dict[str, Any]]:
        """Interest input: entities referenced by the user's own statements."""

        return self.neo4j.execute_query(
            INTEREST_ENTITY_CANDIDATES,
            id=end_user_id,
            limit=limit,
            excluded_names=excluded_names,
            iso_datetime_pattern=iso_datetime_pattern,
            unix_timestamp_pattern=unix_timestamp_pattern,
        )

    def interest_statement_evidence(
        self,
        end_user_id: str,
        *,
        entity_ids: list[str],
        per_entity_limit: int,
        limit: int,
        max_chars: int,
    ) -> list[dict[str, Any]]:
        """Statement context for the interest fallback pass."""

        if not entity_ids:
            return []
        return self.neo4j.execute_query(
            INTEREST_STATEMENT_EVIDENCE,
            id=end_user_id,
            entity_ids=entity_ids,
            per_entity_limit=per_entity_limit,
            limit=limit,
            max_chars=max_chars,
        )
