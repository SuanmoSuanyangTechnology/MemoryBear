from dataclasses import dataclass, field

from app.core.memory.models.graph_models import (
    AssistantConversationEdge,
    AssistantOriginalNode,
    AssistantPrunedEdge,
    AssistantPrunedNode,
    ChunkNode,
    ConversationNode,
    DialogueNode,
    EntityEntityEdge,
    ExtractedEntityNode,
    PerceptualEdge,
    PerceptualNode,
    StatementChunkEdge,
    StatementEntityEdge,
    StatementNode,
    UserSourceEntityEdge,
    UserSourceNode,
)


@dataclass(slots=True)
class MemoryGraphWriteCommand:
    """Complete graph payload committed by one normal-write transaction."""

    dialogue_nodes: list[DialogueNode] = field(default_factory=list)
    chunk_nodes: list[ChunkNode] = field(default_factory=list)
    statement_nodes: list[StatementNode] = field(default_factory=list)
    entity_nodes: list[ExtractedEntityNode] = field(default_factory=list)
    perceptual_nodes: list[PerceptualNode] = field(default_factory=list)
    entity_edges: list[EntityEntityEdge] = field(default_factory=list)
    statement_chunk_edges: list[StatementChunkEdge] = field(default_factory=list)
    statement_entity_edges: list[StatementEntityEdge] = field(default_factory=list)
    perceptual_edges: list[PerceptualEdge] = field(default_factory=list)
    assistant_original_nodes: list[AssistantOriginalNode] = field(default_factory=list)
    assistant_pruned_nodes: list[AssistantPrunedNode] = field(default_factory=list)
    assistant_pruned_edges: list[AssistantPrunedEdge] = field(default_factory=list)
    conversation_nodes: list[ConversationNode] = field(default_factory=list)
    assistant_conversation_edges: list[AssistantConversationEdge] = field(
        default_factory=list
    )
    user_source_nodes: list[UserSourceNode] = field(default_factory=list)
    user_source_edges: list[UserSourceEntityEdge] = field(default_factory=list)
