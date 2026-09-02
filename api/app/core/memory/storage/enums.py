from enum import StrEnum


class StorageBackendType(StrEnum):
    GRAPH_MAIN_WRITE = 'GRAPH_MAIN_WRITE'
    TEXT_MAIN_WRITE = 'TEXT_MAIN_WRITE'
    VECTOR_MAIN_WRITE = 'VECTOR_MAIN_WRITE'

    GRAPH_MAIN_READ = 'GRAPH_MAIN_READ'
    TEXT_MAIN_READ = 'TEXT_MAIN_READ'
    VECTOR_MAIN_READ = 'VECTOR_MAIN_READ'

    GRAPH_NODE = 'GRAPH_NODE'
    TEXT_NODE = 'TEXT_NODE'
    VECTOR_NODE = 'VECTOR_NODE'


class BackendType(StrEnum):
    ELASTIC = 'ELASTIC'
    NEO4J = 'NEO4J'


class MemoryNodeLabel(StrEnum):
    """Base type for production and test-only memory node labels."""


class MemoryNodeType(MemoryNodeLabel):
    ASSISTANT_ORIGINAL = 'AssistantOriginal'
    ASSISTANT_PRUNED = 'AssistantPruned'
    CHUNK = 'Chunk'
    COMMUNITY = 'Community'
    CONVERSATION = 'Conversation'
    DIALOGUE = 'Dialogue'
    EXTRACTED_ENTITY = 'ExtractedEntity'
    MEMORY_SUMMARY = 'MemorySummary'
    PERCEPTUAL = 'Perceptual'
    STATEMENT = 'Statement'
    USER_SOURCE = 'UserSource'


class RelationshipScope(StrEnum):
    """Graph element addressed by relationship projection or sorting."""

    SOURCE = "source"
    RELATIONSHIP = "relationship"
    TARGET = "target"


class MemoryRelationshipType(StrEnum):
    """Neo4j physical relationship types used by the memory graph."""

    BELONGS_TO_COMMUNITY = "BELONGS_TO_COMMUNITY"
    BELONGS_TO_CONVERSATION = "BELONGS_TO_CONVERSATION"
    BELONGS_TO_DIALOG = "BELONGS_TO_DIALOG"
    CONTAINS = "CONTAINS"
    DERIVED_FROM = "DERIVED_FROM"
    DERIVED_FROM_STATEMENT = "DERIVED_FROM_STATEMENT"
    EXTRACTED_RELATIONSHIP = "EXTRACTED_RELATIONSHIP"
    HAS_ORIGINAL_CONTENT = "HAS_ORIGINAL_CONTENT"
    HAS_PERCEPTUAL = "HAS_PERCEPTUAL"
    MENTIONS = "MENTIONS"
    PRUNED_TO = "PRUNED_TO"
    REFERENCES_ENTITY = "REFERENCES_ENTITY"
    RELATES_TO = "RELATES_TO"
    STATEMENT_ENTITY = "STATEMENT_ENTITY"
