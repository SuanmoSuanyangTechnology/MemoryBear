"""Data models for the Memory module.

This package contains all Pydantic models used in the memory system,
including models for messages, dialogues, statements, entities, triplets,
graph nodes/edges, configurations, and deduplication decisions.
"""

# Base response models
from app.core.memory.models.base_response import RobustLLMResponse

# Configuration models
from app.core.memory.models.config_models import (
    LLMConfig,
    ChunkerConfig,
    PruningConfig,
    TemporalSearchParams,
)

# Deduplication models
from app.core.memory.models.dedup_models import (
    EntityDedupDecision,
    EntityDisambDecision,
)

# Graph models (nodes and edges)
from app.core.memory.models.graph_models import (
    # Edges
    Edge,
    ChunkEdge,
    ChunkEntityEdge,
    ChunkDialogEdge,
    StatementChunkEdge,
    StatementEntityEdge,
    EntityEntityEdge,
    # Nodes
    Node,
    DialogueNode,
    StatementNode,
    ChunkNode,
    ExtractedEntityNode,
    MemorySummaryNode,
)

# Message and dialogue models
from app.core.memory.models.message_models import (
    ConversationMessage,
    TemporalValidityRange,
    Statement,
    ConversationContext,
    Chunk,
    DialogData,
)

# Triplet and entity models
from app.core.memory.models.triplet_models import (
    Entity,
    Triplet,
    TripletExtractionResponse,
)

# Ontology scenario models (LLM extracted from scenarios)
from app.core.memory.models.ontology_scenario_models import (
    OntologyClass,
    OntologyExtractionResponse,
)

# Ontology extraction models (for extraction flow)
from app.core.memory.models.ontology_extraction_models import (
    OntologyTypeInfo,
    OntologyTypeList,
)

# Ontology general models (loaded from external ontology files)
from app.core.memory.models.ontology_general_models import (
    OntologyFileFormat,
    GeneralOntologyType,
    GeneralOntologyTypeRegistry,
)

# Variable configuration models
from app.core.memory.models.variate_config import (
    StatementExtractionConfig,
    ForgettingEngineConfig,
    TripletExtractionConfig,
    TemporalExtractionConfig,
    DedupConfig,
    ExtractionPipelineConfig,
)

__all__ = [
    # Base response
    "RobustLLMResponse",
    # Configuration
    "LLMConfig",
    "ChunkerConfig",
    "PruningConfig",
    "TemporalSearchParams",
    # Deduplication
    "EntityDedupDecision",
    "EntityDisambDecision",
    # Graph edges
    "Edge",
    "ChunkEdge",
    "ChunkEntityEdge",
    "ChunkDialogEdge",
    "StatementChunkEdge",
    "StatementEntityEdge",
    "EntityEntityEdge",
    # Graph nodes
    "Node",
    "DialogueNode",
    "StatementNode",
    "ChunkNode",
    "ExtractedEntityNode",
    "MemorySummaryNode",
    # Messages and dialogues
    "ConversationMessage",
    "TemporalValidityRange",
    "Statement",
    "ConversationContext",
    "Chunk",
    "DialogData",
    # Triplets and entities
    "Entity",
    "Triplet",
    "TripletExtractionResponse",
    # Ontology models
    "OntologyClass",
    "OntologyExtractionResponse",
    # Ontology type models for extraction flow
    "OntologyTypeInfo",
    "OntologyTypeList",
    # General ontology type models
    "OntologyFileFormat",
    "GeneralOntologyType",
    "GeneralOntologyTypeRegistry",
    # Variable configuration
    "StatementExtractionConfig",
    "ForgettingEngineConfig",
    "TripletExtractionConfig",
    "TemporalExtractionConfig",
    "DedupConfig",
    "ExtractionPipelineConfig",
]
