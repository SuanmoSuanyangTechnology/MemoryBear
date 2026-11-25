from enum import StrEnum


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
    PREFERENCE = 'Preference'
    RAG_MEMORY = 'RagMemory'
    SCENE_COMMUNITY = 'SceneCommunity'
    SCENE_SUMMARY = 'SceneSummary'
    STATEMENT = 'Statement'
    USER_SOURCE = 'UserSource'
