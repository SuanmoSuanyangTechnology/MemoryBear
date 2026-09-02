from dataclasses import dataclass
from typing import Any

from app.core.memory.storage.enums import MemoryNodeLabel, MemoryNodeType

INDEX_SHARD_COUNT = 5
INDEX_ALIAS_SUFFIX = "_current"
EMBEDDING_DIMS = 1024

FULLTEXT_FIELDS: dict[MemoryNodeLabel, tuple[str, ...]] = {
    MemoryNodeType.STATEMENT: ("statement",),
    MemoryNodeType.CHUNK: ("content",),
    MemoryNodeType.EXTRACTED_ENTITY: (
        "name",
        "description",
        "aliases",
        "description_summary",
        "description_timeline",
    ),
    MemoryNodeType.MEMORY_SUMMARY: ("content",),
    MemoryNodeType.COMMUNITY: ("name", "summary"),
    MemoryNodeType.PERCEPTUAL: ("summary", "topic", "domain", "keywords"),
    MemoryNodeType.ASSISTANT_PRUNED: ("text",),
    MemoryNodeType.DIALOGUE: ("content",),
}

EMBEDDING_FIELDS: dict[MemoryNodeLabel, str] = {
    MemoryNodeType.STATEMENT: "statement_embedding",
    MemoryNodeType.CHUNK: "chunk_embedding",
    MemoryNodeType.EXTRACTED_ENTITY: "name_embedding",
    MemoryNodeType.MEMORY_SUMMARY: "summary_embedding",
    MemoryNodeType.COMMUNITY: "summary_embedding",
    MemoryNodeType.PERCEPTUAL: "summary_embedding",
    MemoryNodeType.DIALOGUE: "dialog_embedding",
    MemoryNodeType.ASSISTANT_PRUNED: "text_embedding",
}


@dataclass(frozen=True, slots=True)
class IndexDefinition:
    """Schema revision and physical generation owned by one MemoryNodeType."""

    name: str
    schema_version: int
    generation: int
    settings: dict[str, Any]
    mappings: dict[str, Any]

    @property
    def alias(self) -> str:
        """Return the stable alias used by all CRUD operations."""
        return f"{self.name}{INDEX_ALIAS_SUFFIX}"


def _index_definition(
        name: str,
        label: MemoryNodeLabel,
        *,
        schema_version: int,
        generation: int,
        prop: dict | None = None
) -> IndexDefinition:
    """Build one independently versioned schema with explicit search mappings."""
    if prop is None:
        prop = dict()
    properties: dict[str, Any] = {
        field: {"type": "text", "analyzer": "cjk"}
        for field in FULLTEXT_FIELDS.get(label, ())
    }
    embedding_field = EMBEDDING_FIELDS.get(label)
    if embedding_field is not None:
        properties[embedding_field] = {
            "type": "dense_vector",
            "dims": EMBEDDING_DIMS,
            "index": True,
            "similarity": "cosine",
        }

    prop.update(properties)

    return IndexDefinition(
        name=name,
        schema_version=schema_version,
        generation=generation,
        settings={"number_of_shards": INDEX_SHARD_COUNT},
        mappings={
            "dynamic_templates": [
                {
                    "strings_as_keywords": {
                        "match_mapping_type": "string",
                        "mapping": {
                            "type": "keyword",
                            "ignore_above": 8191,
                        },
                    }
                }
            ],
            "properties": prop,
        },
    )


INDEX_DEFINITIONS: dict[MemoryNodeLabel, IndexDefinition] = {
    MemoryNodeType.ASSISTANT_ORIGINAL: _index_definition(
        "assistant_original",
        MemoryNodeType.ASSISTANT_ORIGINAL,
        schema_version=2,
        generation=1,
        prop={
            "id": {"type": "keyword"},
            "name": {"type": "text"},
            "end_user_id": {"type": "keyword"},
            "run_id": {"type": "keyword"},
            "created_at": {"type": "date"},
            "pair_id": {"type": "keyword"},
            "dialog_id": {"type": "keyword"},
            "access_count": {"type": "long"},
        }
    ),
    MemoryNodeType.ASSISTANT_PRUNED: _index_definition(
        "assistant_pruned",
        MemoryNodeType.ASSISTANT_PRUNED,
        schema_version=2,
        generation=1,
        prop={
            "id": {"type": "keyword"},
            "name": {"type": "text"},
            "end_user_id": {"type": "keyword"},
            "run_id": {"type": "keyword"},
            "created_at": {"type": "date"},
            "pair_id": {"type": "keyword"},
            "dialog_id": {"type": "keyword"},
            "memory_type": {"type": "keyword"},
        }
    ),
    MemoryNodeType.CHUNK: _index_definition(
        "chunk",
        MemoryNodeType.CHUNK,
        schema_version=2,
        generation=1,
        prop={
            "id": {"type": "keyword"},
            "name": {"type": "text"},
            "end_user_id": {"type": "keyword"},
            "run_id": {"type": "keyword"},
            "created_at": {"type": "date"},
            "dialog_id": {"type": "keyword"},
            "speaker": {"type": "keyword"},
            "sequence_number": {"type": "long"},
            "delete_at": {"type": "date"},
            "topology_score": {"type": "float"},
        }
    ),
    MemoryNodeType.COMMUNITY: _index_definition(
        "community",
        MemoryNodeType.COMMUNITY,
        schema_version=2,
        generation=1,
        prop={
            "community_id": {"type": "keyword"},
            "id": {"type": "keyword"},
            "end_user_id": {"type": "keyword"},
            "member_count": {"type": "long"},
            "updated_at": {"type": "date"},
            "core_entities": {"type": "keyword"},
        }
    ),
    MemoryNodeType.CONVERSATION: _index_definition(
        "conversation",
        MemoryNodeType.CONVERSATION,
        schema_version=2,
        generation=1,
        prop={
            "id": {"type": "keyword"},
            "name": {"type": "text"},
            "end_user_id": {"type": "keyword"},
            "conversation_id": {"type": "keyword"},
            "run_id": {"type": "keyword"},
            "created_at": {"type": "date"},
        }
    ),
    MemoryNodeType.DIALOGUE: _index_definition(
        "dialogue",
        MemoryNodeType.DIALOGUE,
        schema_version=2,
        generation=1,
        prop={
            "id": {"type": "keyword"},
            "uuid": {"type": "keyword"},
            "name": {"type": "text"},
            "end_user_id": {"type": "keyword"},
            "run_id": {"type": "keyword"},
            "ref_id": {"type": "keyword"},
            "created_at": {"type": "date"},
            # dialog_embedding
            "write_mode": {"type": "keyword"},
            "config_id": {"type": "keyword"},
            "delete_at": {"type": "date"},
            "emotion": {"type": "keyword"},
            "emotion_score": {"type": "float"}
        }
    ),
    MemoryNodeType.EXTRACTED_ENTITY: _index_definition(
        "extracted_entity",
        MemoryNodeType.EXTRACTED_ENTITY,
        schema_version=2,
        generation=1,
        prop={
            "id": {"type": "keyword"},
            "end_user_id": {"type": "keyword"},
            "run_id": {"type": "keyword"},
            "created_at": {"type": "date"},
            "entity_idx": {"type": "long"},
            "statement_id": {"type": "keyword"},
            "entity_type": {"type": "keyword"},
            "type_id": {"type": "long"},
            "type_description": {"type": "text"},
            "example": {"type": "text"},
            "event_timeline": {"type": "text"},
            "connect_strength": {"type": "keyword"},
            "importance_score": {"type": "float"},
            "access_history": {"type": "keyword"},
            "access_count": {"type": "long"},
            "is_explicit_memory": {"type": "boolean"},
            "extraction_count": {"type": "long"},
            "core_facts": {"type": "keyword"},
            "traits": {"type": "keyword"},
            "relations": {"type": "keyword"},
            "goals": {"type": "keyword"},
            "interests": {"type": "keyword"},
            "beliefs_or_stances": {"type": "keyword"},
            "anchors": {"type": "keyword"},
            "events": {"type": "keyword"},
            "source": {"type": "keyword"},
            "delete_at": {"type": "date"},
            "topology_score": {"type": "float"},
        }
    ),
    MemoryNodeType.MEMORY_SUMMARY: _index_definition(
        "memory_summary",
        MemoryNodeType.MEMORY_SUMMARY,
        schema_version=2,
        generation=1,
        prop={
            "id": {"type": "keyword"},
            "end_user_id": {"type": "keyword"},
            "run_id": {"type": "keyword"},
            "created_at": {"type": "date"},
            "dialog_id": {"type": "keyword"},
            "chunk_ids": {"type": "keyword"},
            "memory_type": {"type": "keyword"},
            "config_id": {"type": "keyword"},
            "importance_score": {"type": "float"},
            "activation_value": {"type": "float"},
            "access_history": {"type": "keyword"},
            "last_access_time": {"type": "keyword"},
            "access_count": {"type": "long"},
            "original_statement_id": {"type": "keyword"},
            "original_entity_id": {"type": "keyword"},
            "version": {"type": "long"},
            "merged_at": {"type": "date"},
            "delete_at": {"type": "date"},
            "topology_score": {"type": "float"},
        }
    ),
    MemoryNodeType.PERCEPTUAL: _index_definition(
        "perceptual",
        MemoryNodeType.PERCEPTUAL,
        schema_version=2,
        generation=1,
        prop={
            "id": {"type": "keyword"},
            "end_user_id": {"type": "keyword"},
            "perceptual_type": {"type": "long"},
            "file_path": {"type": "keyword"},
            "file_name": {"type": "keyword"},
            "file_ext": {"type": "keyword"},
            "created_at": {"type": "date"},
            "file_type": {"type": "keyword"},
            "topology_score": {"type": "float"},
        }
    ),
    MemoryNodeType.STATEMENT: _index_definition(
        "statement",
        MemoryNodeType.STATEMENT,
        schema_version=2,
        generation=1,
        prop={
            "id": {"type": "keyword"},
            "run_id": {"type": "keyword"},
            "chunk_id": {"type": "keyword"},
            "end_user_id": {"type": "keyword"},
            "stmt_type": {"type": "keyword"},
            "speaker": {"type": "keyword"},
            "emotion_intensity": {"type": "float"},
            "emotion_type": {"type": "keyword"},
            "emotion_keywords": {"type": "keyword"},
            "temporal_info": {"type": "text"},
            "created_at": {"type": "date"},
            "valid_at": {"type": "date"},
            "invalid_at": {"type": "date"},
            "importance_score": {"type": "float"},
            "access_history": {"type": "keyword"},
            "access_count": {"type": "long"},
            "dialog_at": {"type": "date"},
            "has_unsolved_reference": {"type": "boolean"},
            "is_permanent": {"type": "boolean"},
            "delete_at": {"type": "date"},
            "topology_score": {"type": "float"},
        }
    ),
    MemoryNodeType.USER_SOURCE: _index_definition(
        "user_source",
        MemoryNodeType.USER_SOURCE,
        schema_version=2,
        generation=1,
        prop={
            "id": {"type": "keyword"},
            "end_user_id": {"type": "keyword"},
            "run_id": {"type": "keyword"},
            "created_at": {"type": "date"},
            "message_seq": {"type": "long"},
            "original_text": {"type": "text"},
            "pruned_text": {"type": "text"},
        }
    ),
}


def get_index_definition(label: MemoryNodeLabel) -> IndexDefinition:
    """Return the manually registered definition for a node type."""
    if not isinstance(label, MemoryNodeLabel):
        raise KeyError(f"node type - {label} not supported")
    try:
        return INDEX_DEFINITIONS[label]
    except KeyError as exc:
        raise KeyError(
            f"Elasticsearch index definition for {label.name} is missing"
        ) from exc


def get_index_name(label: MemoryNodeLabel) -> str:
    """Return the stable alias used by CRUD operations for a node type."""
    return get_index_definition(label).alias


def validate_definition_registry() -> None:
    """Validate that every node type has one usable, unique definition."""
    missing = set(MemoryNodeType).difference(INDEX_DEFINITIONS)
    invalid_labels = [
        label
        for label in INDEX_DEFINITIONS
        if not isinstance(label, MemoryNodeLabel)
    ]
    if missing or invalid_labels:
        raise RuntimeError(
            "Elasticsearch index definitions must include every MemoryNodeType "
            "and use only MemoryNodeLabel keys; "
            f"missing={sorted(label.name for label in missing)!r}, "
            f"invalid={sorted(str(label) for label in invalid_labels)!r}"
        )

    definitions = tuple(INDEX_DEFINITIONS.values())
    names = [definition.name for definition in definitions]
    aliases = [definition.alias for definition in definitions]
    if len(names) != len(set(names)) or len(aliases) != len(set(aliases)):
        raise RuntimeError(
            "Elasticsearch index definition names and aliases must be unique"
        )
    invalid_versions = [
        definition.name
        for definition in definitions
        if any(
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 1
            for value in (
                definition.schema_version,
                definition.generation,
            )
        )
    ]
    if invalid_versions:
        raise RuntimeError(
            "Elasticsearch schema versions and generations must be positive "
            f"integers; invalid={invalid_versions!r}"
        )
