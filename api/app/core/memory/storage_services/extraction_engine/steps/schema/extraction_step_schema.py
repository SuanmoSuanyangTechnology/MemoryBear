"""Pydantic models for base extraction pipeline inputs and outputs.

Covers the core (critical) stages: Statement extraction, Triplet extraction,
Embedding generation, and shared types used across stages.

Malformed LLM JSON will raise ``ValidationError`` and trigger stage-level retry.
"""

from typing import Dict, List, Optional
from pydantic import BaseModel, Field


# ── Shared types ──


class MessageItem(BaseModel):
    """Single conversation message."""

    role: str  # "User" / "Assistant"
    msg: str


class SupportingContext(BaseModel):
    """Dialogue context window (used for pronoun resolution, etc.)."""

    msgs: List[MessageItem] = Field(default_factory=list)


# ── Statement extraction ──
class StatementStepInput(BaseModel):
    """Input for StatementTemporalExtractionStep."""

    chunk_id: str
    end_user_id: str
    target_content: str
    target_message_date: str
    dialog_at: str = ""   # ISO 8601 timestamp of the source message; used as "now" for relative time resolution
    supporting_context: SupportingContext


class StatementStepOutput(BaseModel):
    """Single extracted statement (including temporal info)."""

    statement_id: str
    statement_text: str
    statement_type: str   # FACT / OPINION / PREDICTION / SUGGESTION
    temporal_type: str    # STATIC / DYNAMIC / ATEMPORAL
    # relevance: str        # RELEVANT / IRRELEVANT
    speaker: str          # "user" / "assistant"
    has_emotional_state: bool = False  # Whether statement reflects user's emotional state
    valid_at: str         # ISO 8601 or "NULL"
    invalid_at: str       # ISO 8601 or "NULL"
    has_unsolved_reference: bool = False  # Whether the statement has unresolved references
    dialog_at: str = ""   # Passed through from input; carried into TripletStepInput


# ── Triplet extraction ──
class TripletStepInput(BaseModel):
    """Input for TripletExtractionStep."""

    statement_id: str
    statement_text: str
    statement_type: str
    temporal_type: str
    supporting_context: SupportingContext
    speaker: str
    dialog_at: str = ""   # ISO 8601 timestamp of the source message; helps LLM ground entity descriptions in time
    valid_at: str
    invalid_at: str
    has_unsolved_reference: bool = False  # From upstream statement extraction


class EntityItem(BaseModel):
    """Single entity extracted during triplet extraction."""

    entity_idx: int
    name: str
    type: str
    type_id: Optional[int] = None
    type_description: str = ""
    description: str
    is_explicit_memory: bool = False


class TripletItem(BaseModel):
    """Single triplet (subject-predicate-object) relationship."""

    subject_name: str
    subject_id: int
    predicate: str
    predicate_id: Optional[int] = None
    predicate_surface: str
    predicate_description: str = ""
    object_name: str
    object_id: int


class TripletStepOutput(BaseModel):
    """Output of TripletExtractionStep."""

    entities: List[EntityItem] = Field(default_factory=list)
    triplets: List[TripletItem] = Field(default_factory=list)


# ── Embedding generation ──
class EmbeddingStepInput(BaseModel):
    """Input for EmbeddingStep.

    Each dict maps an ID to the text that should be embedded.
    Fields can be left empty for partial embedding runs.
    """

    statement_texts: Dict[str, str] = Field(default_factory=dict)
    chunk_texts: Dict[str, str] = Field(default_factory=dict)
    dialog_texts: List[str] = Field(default_factory=list)
    entity_names: Dict[str, str] = Field(default_factory=dict)
    entity_descriptions: Dict[str, str] = Field(default_factory=dict)


class EmbeddingStepOutput(BaseModel):
    """Output of EmbeddingStep."""

    statement_embeddings: Dict[str, List[float]] = Field(default_factory=dict)
    chunk_embeddings: Dict[str, List[float]] = Field(default_factory=dict)
    dialog_embeddings: List[List[float]] = Field(default_factory=list)
    entity_embeddings: Dict[str, List[float]] = Field(default_factory=dict)