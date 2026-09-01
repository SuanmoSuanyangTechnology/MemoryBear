import asyncio

from app.core.memory.storage.enums import MemoryNodeType, MemoryRelationshipType
from app.core.memory.storage.models import (
    FilterCondition,
    FilterOperator,
    NodeFilter,
    RelationshipFilter,
    RelationshipPattern,
    RelationshipProjection,
    RelationshipProjectionField,
    RelationshipProjectionScope,
)
from app.core.memory.storage.service import get_storage_service


async def search_related_entities(
        end_user_id: str,
        source_id: str,
        predicate_ids: list[int],
) -> list[dict]:
    """Return active entities related to a source by ontology predicates."""
    if not predicate_ids:
        return []

    pattern = RelationshipPattern(
        relationship_type=MemoryRelationshipType.EXTRACTED_RELATIONSHIP,
        directed=False,
        source_label=MemoryNodeType.EXTRACTED_ENTITY,
        target_label=MemoryNodeType.EXTRACTED_ENTITY,
    )
    projection = RelationshipProjection.of(
        RelationshipProjectionField(
            scope=RelationshipProjectionScope.TARGET,
            field="id",
        ),
        RelationshipProjectionField(
            scope=RelationshipProjectionScope.SOURCE,
            field="name",
            alias="source_name",
        ),
        RelationshipProjectionField(
            scope=RelationshipProjectionScope.RELATIONSHIP,
            field="predicate",
            alias="relation_predicate",
        ),
        RelationshipProjectionField(
            scope=RelationshipProjectionScope.TARGET,
            field="name",
            alias="target_name",
        ),
    )

    service = get_storage_service()
    result = await service.search_relationships_by_graph(
        pattern=pattern,
        rel_filter=RelationshipFilter(
            relationship=NodeFilter(
                conditions=(
                    FilterCondition(
                        field="predicate_id",
                        operator=FilterOperator.IN,
                        value=predicate_ids,
                    ),
                ),
            ),
            source=NodeFilter.all_of(
                FilterCondition(field="end_user_id", value=end_user_id),
                FilterCondition(field="id", value=source_id),
                FilterCondition(
                    field="delete_at",
                    operator=FilterOperator.EXISTS,
                    value=False,
                ),
            ),
            target=NodeFilter.all_of(
                FilterCondition(field="end_user_id", value=end_user_id),
                FilterCondition(
                    field="delete_at",
                    operator=FilterOperator.EXISTS,
                    value=False,
                ),
            ),
        ),
        projection=projection,
    )
    return [item.data for item in result.items]


async def get_user_sources_for_entities(
        end_user_id: str,
        entity_ids: list[str],
) -> list[dict]:
    """Return original UserSource text records for extracted entity IDs."""
    if not entity_ids:
        return []

    pattern = RelationshipPattern(
        relationship_type=MemoryRelationshipType.HAS_ORIGINAL_CONTENT,
        directed=True,
        source_label=MemoryNodeType.USER_SOURCE,
        target_label=MemoryNodeType.EXTRACTED_ENTITY,
    )
    projection = RelationshipProjection.of(
        RelationshipProjectionField(
            scope=RelationshipProjectionScope.TARGET,
            field="id",
            alias="entity_id",
        ),
        RelationshipProjectionField(
            scope=RelationshipProjectionScope.SOURCE,
            field="original_text",
        ),
    )

    service = get_storage_service()
    result = await service.search_relationships_by_graph(
        pattern=pattern,
        rel_filter=RelationshipFilter(
            source=NodeFilter.eq("end_user_id", end_user_id),
            target=NodeFilter(
                conditions=(
                    FilterCondition(
                        field="id",
                        operator=FilterOperator.IN,
                        value=entity_ids,
                    ),
                ),
            ),
        ),
        projection=projection,
    )
    return [
        {
            "entity_id": item.data["entity_id"],
            "original_text": item.data["original_text"],
        }
        for item in result.items
        if item.data.get("entity_id") and item.data.get("original_text")
    ]


async def get_entity_pair_relations(
        end_user_id: str,
        pairs: list[dict[str, str]],
) -> list[dict]:
    """Return relationships for exact source-target entity pairs."""
    valid_pairs = [
        pair
        for pair in pairs
        if pair.get("source_id") and pair.get("target_id")
    ]
    if not valid_pairs:
        return []

    pattern = RelationshipPattern(
        relationship_type=None,
        directed=False,
        source_label=MemoryNodeType.EXTRACTED_ENTITY,
        target_label=MemoryNodeType.EXTRACTED_ENTITY,
    )
    projection = RelationshipProjection.of(
        RelationshipProjectionField(
            scope=RelationshipProjectionScope.SOURCE,
            field="id",
            alias="source_id",
        ),
        RelationshipProjectionField(
            scope=RelationshipProjectionScope.SOURCE,
            field="name",
            alias="source_name",
        ),
        RelationshipProjectionField(
            scope=RelationshipProjectionScope.RELATIONSHIP,
            field="predicate",
            alias="relation_predicate",
        ),
        RelationshipProjectionField(
            scope=RelationshipProjectionScope.TARGET,
            field="id",
            alias="target_id",
        ),
        RelationshipProjectionField(
            scope=RelationshipProjectionScope.TARGET,
            field="name",
            alias="target_name",
        ),
    )

    service = get_storage_service()
    results = await asyncio.gather(
        *(
            service.search_relationships_by_graph(
                pattern=pattern,
                rel_filter=RelationshipFilter(
                    source=NodeFilter.all_of(
                        FilterCondition(
                            field="end_user_id",
                            value=end_user_id,
                        ),
                        FilterCondition(
                            field="id",
                            value=pair["source_id"],
                        ),
                        FilterCondition(
                            field="delete_at",
                            operator=FilterOperator.EXISTS,
                            value=False,
                        ),
                    ),
                    target=NodeFilter.all_of(
                        FilterCondition(
                            field="id",
                            value=pair["target_id"],
                        ),
                        FilterCondition(
                            field="delete_at",
                            operator=FilterOperator.EXISTS,
                            value=False,
                        ),
                    ),
                ),
                projection=projection,
            )
            for pair in valid_pairs
        )
    )
    return [item.data for result in results for item in result.items]
