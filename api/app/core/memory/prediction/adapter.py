from __future__ import annotations

import asyncio
from typing import Any

from app.repositories.neo4j.neo4j_connector import Neo4jConnector

from .schemas import EntityRecord, MemoryStatement, RelationRecord, UserProfile


_USER_PROFILE = """
MATCH (u:ExtractedEntity {end_user_id: $end_user_id})
WHERE u.delete_at IS NULL
  AND (u.entity_type = '用户' OR toLower(u.name) IN ['用户', 'user'])
WITH u, size(coalesce(u.core_facts, [])) + size(coalesce(u.traits, [])) +
 size(coalesce(u.relations, [])) + size(coalesce(u.goals, [])) +
 size(coalesce(u.interests, [])) + size(coalesce(u.beliefs_or_stances, [])) +
 size(coalesce(u.anchors, [])) + size(coalesce(u.events, [])) AS richness
RETURN u.id AS id, coalesce(u.name, '用户') AS name,
 coalesce(u.core_facts, []) AS core_facts, coalesce(u.traits, []) AS traits,
 coalesce(u.relations, []) AS relations, coalesce(u.goals, []) AS goals,
 coalesce(u.interests, []) AS interests,
 coalesce(u.beliefs_or_stances, []) AS beliefs_or_stances,
 coalesce(u.anchors, []) AS anchors, coalesce(u.events, []) AS events
ORDER BY richness DESC LIMIT 1
"""

_ENTITIES = """
MATCH (e:ExtractedEntity {end_user_id: $end_user_id})
WHERE e.delete_at IS NULL
OPTIONAL MATCH (s:Statement {end_user_id: $end_user_id})-[:REFERENCES_ENTITY]->(e)
WHERE s.delete_at IS NULL
WITH e, count(DISTINCT s) AS ref_count
WHERE ref_count >= $min_refs
RETURN e.id AS id, e.name AS name, e.entity_type AS entity_type,
 coalesce(e.description, '') AS description, coalesce(e.example, '') AS example,
 coalesce(e.aliases, []) AS aliases, ref_count
ORDER BY ref_count DESC
"""

_RELATIONS = """
MATCH (source:ExtractedEntity {end_user_id: $end_user_id})
      -[r:EXTRACTED_RELATIONSHIP]->
      (other:ExtractedEntity {end_user_id: $end_user_id})
WHERE source.delete_at IS NULL AND other.delete_at IS NULL
RETURN source.id AS source_id, source.name AS source_name,
 source.entity_type AS source_type,
 coalesce(r.predicate, r.relation_type, '') AS predicate,
 other.id AS other_id, other.name AS other_name, other.entity_type AS other_type,
 coalesce(r.statement, '') AS evidence, toString(r.valid_at) AS valid_at
ORDER BY valid_at
"""

_STATEMENTS = """
MATCH (s:Statement {end_user_id: $end_user_id})
WHERE s.delete_at IS NULL
OPTIONAL MATCH (s)-[:REFERENCES_ENTITY]->(e:ExtractedEntity {end_user_id: $end_user_id})
WITH s, collect(DISTINCT e.id) AS entity_ids, collect(DISTINCT e.name) AS entity_names
RETURN s.id AS id, coalesce(s.statement, s.text, '') AS text,
 coalesce(s.statement_type, s.stmt_type, 'FACT') AS stmt_type, s.speaker AS speaker,
 s.emotion_type AS emotion_type, s.emotion_intensity AS emotion_intensity,
 toString(coalesce(s.dialog_at, s.created_at)) AS dialog_at,
 toString(s.valid_at) AS valid_at, entity_ids, entity_names
ORDER BY dialog_at
"""

_AS_OF = """
MATCH (s:Statement {end_user_id: $end_user_id}) WHERE s.delete_at IS NULL
RETURN toString(max(coalesce(s.dialog_at, s.created_at))) AS as_of
"""


class GraphAdapter:
    def __init__(self, connector: Neo4jConnector | None = None) -> None:
        self._connector = connector or Neo4jConnector(shared_driver=True)

    async def _query(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        return await self._connector.execute_query(cypher, json_format=True, **params)

    async def load_profile(self, end_user_id: str) -> UserProfile | None:
        rows = await self._query(_USER_PROFILE, end_user_id=end_user_id)
        return UserProfile(entity_id=rows[0].pop("id"), **rows[0]) if rows else None

    async def load_entities(self, end_user_id: str) -> list[EntityRecord]:
        rows = await self._query(_ENTITIES, end_user_id=end_user_id, min_refs=0)
        return [EntityRecord(**row) for row in rows if row.get("id") and row.get("name")]

    async def load_relations(self, end_user_id: str) -> list[RelationRecord]:
        rows = await self._query(_RELATIONS, end_user_id=end_user_id)
        return [RelationRecord(**row) for row in rows if row.get("other_id") and row.get("other_name")]

    async def load_statements(self, end_user_id: str) -> list[MemoryStatement]:
        rows = await self._query(_STATEMENTS, end_user_id=end_user_id)
        return [MemoryStatement(**row) for row in rows if row.get("id") and row.get("text")]

    async def load_as_of(self, end_user_id: str) -> str | None:
        rows = await self._query(_AS_OF, end_user_id=end_user_id)
        return rows[0].get("as_of") if rows else None

    async def load_material(self, end_user_id: str) -> tuple[UserProfile | None, list[EntityRecord], list[MemoryStatement], str | None]:
        profile, entities, statements, as_of = await asyncio.gather(
            self.load_profile(end_user_id), self.load_entities(end_user_id),
            self.load_statements(end_user_id), self.load_as_of(end_user_id),
        )
        return profile, entities, statements, as_of
