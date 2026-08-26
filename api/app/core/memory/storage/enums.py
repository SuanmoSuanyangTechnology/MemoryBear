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
