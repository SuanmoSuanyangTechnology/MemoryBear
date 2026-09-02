"""TODO: Current index management is pending migration from app/repositories/neo4j/create_indexes.py."""
from app.core.memory.storage.enums import MemoryNodeType, MemoryNodeLabel

FULLTEXT_ANNC: dict[MemoryNodeLabel, str] = {
    MemoryNodeType.STATEMENT: "statementsFulltext",
    MemoryNodeType.CHUNK: "chunksFulltext",
    MemoryNodeType.EXTRACTED_ENTITY: "entitiesFulltext",
    MemoryNodeType.MEMORY_SUMMARY: "summariesFulltext",
    MemoryNodeType.COMMUNITY: "communityFulltext",
    MemoryNodeType.PERCEPTUAL: "perceptualFulltext",
    MemoryNodeType.ASSISTANT_PRUNED: "assistantPrunedFulltext",
    MemoryNodeType.DIALOGUE: "dialogueFulltext",
}

EMBEDDING_FIELDS: dict[MemoryNodeLabel, str] = {
    MemoryNodeType.STATEMENT: "statement_embedding",
    MemoryNodeType.CHUNK: "chunk_embedding",
    MemoryNodeType.EXTRACTED_ENTITY: "name_embedding",
    MemoryNodeType.MEMORY_SUMMARY: "summary_embedding",
    MemoryNodeType.COMMUNITY: "community_embedding",
    MemoryNodeType.PERCEPTUAL: "summary_embedding",
    MemoryNodeType.DIALOGUE: "dialog_embedding",
}
