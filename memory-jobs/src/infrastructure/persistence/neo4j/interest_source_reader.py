"""Neo4j reads behind interest distribution."""

from __future__ import annotations

from src.domain.interest.read_models import InterestCandidate, StatementEvidence
from src.infrastructure.neo4j import Neo4jManager

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


class Neo4jInterestSourceReader:
    def __init__(self, neo4j: Neo4jManager) -> None:
        self._neo4j = neo4j

    def candidates(
        self,
        end_user_id: str,
        *,
        limit: int,
        excluded_names: list[str],
        iso_datetime_pattern: str,
        unix_timestamp_pattern: str,
    ) -> list[InterestCandidate]:
        rows = self._neo4j.execute_query(
            INTEREST_ENTITY_CANDIDATES,
            id=end_user_id,
            limit=limit,
            excluded_names=excluded_names,
            iso_datetime_pattern=iso_datetime_pattern,
            unix_timestamp_pattern=unix_timestamp_pattern,
        )
        return [
            InterestCandidate(
                entity_id=str(row["entity_id"]),
                name=str(row["name"]),
                entity_type=str(row.get("entity_type") or ""),
                frequency=int(row["frequency"]),
            )
            for row in rows
        ]

    def evidence(
        self,
        end_user_id: str,
        *,
        entity_ids: list[str],
        per_entity_limit: int,
        limit: int,
        max_chars: int,
    ) -> list[StatementEvidence]:
        if not entity_ids:
            return []
        rows = self._neo4j.execute_query(
            INTEREST_STATEMENT_EVIDENCE,
            id=end_user_id,
            entity_ids=entity_ids,
            per_entity_limit=per_entity_limit,
            limit=limit,
            max_chars=max_chars,
        )
        return [
            StatementEvidence(
                statement_id=str(row["statement_id"]),
                entity_ids=tuple(str(entity_id) for entity_id in row["entity_ids"]),
                statement_text=str(row.get("statement_text") or ""),
            )
            for row in rows
        ]
