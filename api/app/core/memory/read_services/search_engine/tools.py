import logging

from langchain.tools import tool
from pydantic import BaseModel, Field

from app.core.memory.enums import Neo4jNodeType
from app.core.memory.models.service_models import MemoryContext
from app.repositories.neo4j.cypher_queries import FETCH_USER_SOURCES_FOR_ENTITIES
from app.repositories.neo4j.graph_search import search_by_fulltext, search_graph_by_relationship, search_user_metadata
from app.repositories.neo4j.neo4j_connector import Neo4jConnector

logger = logging.getLogger(__name__)


class EntitySearchInput(BaseModel):
    name: str = Field(description="Entity name to search for, supports partial matching")


class RelationSearchInput(BaseModel):
    source_id: str | None = Field(default=None, description="Starting node ID for relation node query")
    relation_predicates: list[int] = Field(description="Relational predicate IDs to retrieve, supports searching multiple relations at once. Must use numeric IDs: 1=别名属于, 2=属于类型, 3=位于, 4=前往, 5=组成部分, 6=拥有, 7=使用, 8=创建了, 9=了解, 10=偏好, 11=负责, 12=沟通于, 13=关联于")


class UserSourceLookupInput(BaseModel):
    entity_ids: list[str] = Field(description="List of ExtractedEntity IDs to trace back to their original user messages (UserSource nodes)")


def make_entity_search_tool(ctx: MemoryContext):
    @tool(args_schema=EntitySearchInput)
    async def entity_search_tool(name: str) -> list[dict]:
        """Search for entities by name in the knowledge graph. Use this to look up entity IDs by name before using them as source_id in relation_search_tool.

        Args:
            name: Entity name or partial name to search for.

        Returns:
            [{"id": "entity id", "name": "entity name", "entity_type": "entity type"}, ...]
        """
        async with Neo4jConnector(shared_driver=True) as connector:
            res = await search_by_fulltext(
                connector=connector,
                node_type=Neo4jNodeType.EXTRACTEDENTITY,
                end_user_id=ctx.end_user_id,
                query=name,
                limit=10,
            )
            return [{"id": r["id"], "name": r["name"], "entity_type": r.get("entity_type")} for r in res]

    return entity_search_tool


def make_relation_search_tool(ctx: MemoryContext):
    @tool(args_schema=RelationSearchInput)
    async def relation_search_tool(relation_predicates: list[int], source_id: str | None = None) -> list[dict]:
        """Query the knowledge graph for entities connected to a source node by one or more relation predicate IDs.

        Omit source_id to start from the user's own entity node. Use this to discover what the user owns, knows, prefers, uses, created, visits, or is related to.

        Args:
            source_id: Starting entity node ID, defaults to the user entity if omitted.
            relation_predicates: Numeric predicate IDs to query. Supports multiple predicates at once.
                1=别名属于, 2=属于类型, 3=位于, 4=前往, 5=组成部分, 6=拥有, 7=使用,
                8=创建了, 9=了解, 10=偏好, 11=负责, 12=沟通于, 13=关联于

        Returns:
            [{"id": "target entity id", "source_name": "source entity name", "relation_predicate": "predicate used", "target_name": "target entity name"}, ...]
        """
        async with Neo4jConnector(shared_driver=True) as connector:
            if not source_id:
                source_id = (await search_user_metadata(connector, ctx.end_user_id)).get("id")
            res = await search_graph_by_relationship(
                connector=connector,
                end_user_id=ctx.end_user_id,
                source_id=source_id,
                predicates=relation_predicates,
            )
            logger.info(f"[relation_search_tool] source:{source_id}, predicates:{relation_predicates}, res:{res}")
            return res

    return relation_search_tool


def make_user_source_lookup_tool(ctx: MemoryContext):
    @tool(args_schema=UserSourceLookupInput)
    async def user_source_lookup_tool(entity_ids: list[str]) -> list[dict]:
        """Trace back from ExtractedEntity nodes to their original UserSource messages.

        Given a list of entity IDs, queries the HAS_ORIGINAL_CONTENT edges to find
        the original user messages where these entities were first mentioned.
        Use this when you need the full original context of an entity to better
        understand relationships or determine relevance.

        Args:
            entity_ids: List of ExtractedEntity IDs to trace back.

        Returns:
            [{"entity_id": "entity id", "original_text": "original user message text"}, ...]
        """
        if not entity_ids:
            return []

        async with Neo4jConnector(shared_driver=True) as connector:
            records = await connector.execute_query(
                FETCH_USER_SOURCES_FOR_ENTITIES,
                entity_ids=entity_ids,
                end_user_id=ctx.end_user_id,
            )

        result = []
        for rec in records:
            eid = rec.get("entity_id", "")
            text = rec.get("original_text", "")
            if eid and text:
                result.append({"entity_id": eid, "original_text": text})

        logger.info(f"[user_source_lookup_tool] entity_ids:{entity_ids}, found:{len(result)}")
        return result

    return user_source_lookup_tool
