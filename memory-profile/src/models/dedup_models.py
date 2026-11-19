"""Models for entity deduplication and disambiguation decisions.

This module contains Pydantic models for structured LLM responses
during entity deduplication and disambiguation processes.

Classes:
    EntityDedupDecision: Decision model for entity deduplication
    EntityDisambDecision: Decision model for entity disambiguation
"""

from typing import Optional
from pydantic import BaseModel, Field


class EntityDedupDecision(BaseModel):
    """Structured decision returned by LLM for entity deduplication.

    This model represents the LLM's decision on whether two entities
    refer to the same real-world entity and should be merged.

    Note: Aliases are extracted during the triplet extraction phase and automatically
    merged during entity merging. LLM only needs to decide whether to merge and which
    entity to keep as canonical.

    Attributes:
        same_entity: Whether the two entities refer to the same real-world entity
        confidence: Model confidence in the decision (0.0 to 1.0)
        canonical_idx: Index of the canonical entity to keep when merging (0 or 1, -1 if not applicable)
        reason: Brief rationale for the decision (1-3 sentences, kept for audit)
    """
    same_entity: bool = Field(..., description="Two entities refer to the same entity")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence of the decision")
    canonical_idx: int = Field(..., description="Index of canonical entity among the pair: 0 or 1; -1 if not applicable")
    reason: str = Field(..., description="Short rationale, 1-3 sentences")


class EntityDisambDecision(BaseModel):
    """Structured disambiguation decision for same-name but different-type entities.

    This model represents the LLM's decision on whether two entities with
    the same name but different types should be merged or kept separate.

    Note: Aliases are extracted during the triplet extraction phase and automatically
    merged during entity merging. LLM only needs to decide whether to merge and which
    entity to keep as canonical.

    Attributes:
        should_merge: Whether the two entities should be merged despite type difference
        confidence: Model confidence in the decision (0.0 to 1.0)
        canonical_idx: Index of the canonical entity to keep when merging (0 or 1, -1 if not applicable)
        block_pair: If True, this pair should be blocked from fuzzy/auto merges
        suggested_type: Optional unified type to apply when should_merge is True
        reason: Brief rationale for audit and analysis (1-3 sentences)
    """
    should_merge: bool = Field(..., description="Merge the pair despite type difference")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence of the decision")
    canonical_idx: int = Field(..., description="Index of canonical entity among the pair: 0 or 1; -1 if not applicable")
    block_pair: bool = Field(False, description="Block this pair from fuzzy or heuristic merges")
    suggested_type: Optional[str] = Field(None, description="Unified entity type when merging")
    reason: str = Field(..., description="Short rationale, 1-3 sentences")
