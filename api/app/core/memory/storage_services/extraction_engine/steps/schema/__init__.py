"""Schema package for ExtractionStep inputs and outputs.

Re-exports all models for convenient access:
    from .schema import StatementStepInput, EmotionStepOutput, ...
"""

from .extraction_step_schema import (
    EmbeddingStepInput,
    EmbeddingStepOutput,
    EntityItem,
    MessageItem,
    StatementStepInput,
    StatementStepOutput,
    SupportingContext,
    TripletItem,
    TripletStepInput,
    TripletStepOutput,
)
from .sidecar_step_schema import (
    EmotionStepInput,
    EmotionStepOutput,
)

__all__ = [
    # Shared
    "MessageItem",
    "SupportingContext",
    # Statement
    "StatementStepInput",
    "StatementStepOutput",
    # Triplet
    "TripletStepInput",
    "TripletStepOutput",
    "EntityItem",
    "TripletItem",
    # Embedding
    "EmbeddingStepInput",
    "EmbeddingStepOutput",
    # Sidecar — Emotion
    "EmotionStepInput",
    "EmotionStepOutput",
]
