"""Variable configuration models for extraction pipeline components.

This module contains Pydantic models for configuring various aspects
of the extraction pipeline, including statement extraction, triplet extraction,
temporal extraction, deduplication, and forgetting mechanisms.

Classes:
    StatementExtractionConfig: Configuration for statement extraction
    ForgettingEngineConfig: Configuration for forgetting engine
    TripletExtractionConfig: Configuration for triplet extraction
    TemporalExtractionConfig: Configuration for temporal extraction
    DedupConfig: Configuration for entity deduplication
    ExtractionPipelineConfig: Combined configuration for entire pipeline
"""

from typing import Optional
from pydantic import BaseModel, Field


class StatementExtractionConfig(BaseModel):
    """Configuration for statement extraction behavior.

    Attributes:
        statement_granularity: Granularity level (1-3):
            - 1: Split sentences into different statements
            - 2: Sentence-level statements
            - 3: Combine sentences, shorten long statements
        temperature: LLM temperature for statement extraction (0-2, default: 0.1)
        include_dialogue_context: Whether to include full dialogue context
        max_dialogue_context_chars: Maximum characters from dialogue context (default: 2000)
    """
    statement_granularity: Optional[int] = Field(None, ge=1, le=3, description="Granularity of statements to extract, level 1 to 3")
    temperature: Optional[float] = Field(0.1, ge=0, le=2, description="LLM temperature for statement extraction")
    include_dialogue_context: bool = Field(True, description="Whether to include full dialogue context in extraction")
    max_dialogue_context_chars: Optional[int] = Field(2000, ge=100, description="Maximum number of characters to include from dialogue context")


class ForgettingEngineConfig(BaseModel):
    """Configuration for the forgetting engine.

    The forgetting engine implements a memory decay mechanism based on
    time and memory strength parameters.

    Attributes:
        offset: Minimum retention level (0-1, prevents complete forgetting, default: 0.1)
        lambda_time: Lambda parameter controlling time decay effect (default: 0.1)
        lambda_mem: Lambda parameter controlling memory strength effect (default: 1.0)
    """
    offset: float = Field(0.1, ge=0.0, le=1.0, description="Minimum retention level (prevents complete forgetting).")
    lambda_time: float = Field(0.1, gt=0.0, description="Lambda parameter controlling time effect.")
    lambda_mem: float = Field(1.0, gt=0.0, description="Lambda parameter controlling memory strength effect.")


class TripletExtractionConfig(BaseModel):
    """Configuration for triplet extraction behavior.

    Attributes:
        temperature: LLM temperature for triplet extraction (0-2, default: 0.1)
        enable_entity_normalization: Whether to normalize entity names (default: True)
        confidence_threshold: Minimum confidence for extracted triplets (0-1, default: 0.7)
    """
    temperature: Optional[float] = Field(0.1, ge=0, le=2, description="LLM temperature for triplet extraction")
    enable_entity_normalization: bool = Field(True, description="Whether to normalize entity names")
    confidence_threshold: Optional[float] = Field(0.7, ge=0, le=1, description="Minimum confidence threshold for extracted triplets")


class TemporalExtractionConfig(BaseModel):
    """Configuration for temporal extraction behavior.

    Attributes:
        temperature: LLM temperature for temporal extraction (0-2, default: 0.1)
    """
    temperature: Optional[float] = Field(0.1, ge=0, le=2, description="LLM temperature for temporal extraction")


class DedupConfig(BaseModel):
    """Configuration for entity deduplication behavior.

    This configuration controls the multi-stage deduplication process,
    including fuzzy matching, LLM-based deduplication, and disambiguation.

    Attributes:
        enable_llm_dedup_blockwise: Enable blockwise LLM-driven deduplication (default: False)
        enable_llm_disambiguation: Enable LLM disambiguation for same-name different-type entities (default: False)
        enable_llm_fallback_only_on_borderline: Only trigger LLM when borderline pairs exist (default: True)
        fuzzy_name_threshold_strict: Strict threshold for name similarity (0-1, default: 0.90)
        fuzzy_type_threshold_strict: Strict threshold for type similarity (0-1, default: 0.75)
        fuzzy_overall_threshold: Overall similarity threshold to merge (0-1, default: 0.82)
        fuzzy_unknown_type_name_threshold: Name threshold when entity type is UNKNOWN (0-1, default: 0.92)
        fuzzy_unknown_type_type_threshold: Type threshold when entity type is UNKNOWN (0-1, default: 0.50)
        name_weight: Weight of name similarity in overall score (0-1, default: 0.50)
        desc_weight: Weight of description similarity in overall score (0-1, default: 0.30)
        type_weight: Weight of type similarity in overall score (0-1, default: 0.20)
        context_bonus: Bonus when entities co-occur in same statements (0-0.2, default: 0.03)
        llm_fallback_floor: Lower bound for borderline score (0-1, default: 0.76)
        llm_fallback_ceiling: Upper bound for borderline score (0-1, default: 0.82)
        llm_block_size: Entities per block for LLM dedup (1-500, default: 50)
        llm_block_concurrency: Concurrent blocks processed by LLM (1-64, default: 4)
        llm_pair_concurrency: Concurrent pairwise decisions per block (1-64, default: 4)
        llm_max_rounds: Maximum LLM iterative dedup rounds (1-10, default: 3)
    """
    # LLM deduplication toggles
    enable_llm_dedup_blockwise: bool = Field(False, description="Toggle blockwise LLM-driven deduplication")
    enable_llm_disambiguation: bool = Field(False, description="Toggle LLM-driven disambiguation for same-name different-type entities")
    enable_llm_fallback_only_on_borderline: bool = Field(True, description="Trigger LLM dedup only when borderline pairs are detected in fuzzy stage")

    # Fuzzy match thresholds
    fuzzy_name_threshold_strict: float = Field(0.90, ge=0, le=1, description="Strict threshold for name similarity")
    fuzzy_type_threshold_strict: float = Field(0.75, ge=0, le=1, description="Strict threshold for type similarity")
    fuzzy_overall_threshold: float = Field(0.82, ge=0, le=1, description="Overall similarity threshold to merge")

    # Specialized thresholds when type is UNKNOWN
    fuzzy_unknown_type_name_threshold: float = Field(0.92, ge=0, le=1, description="Name threshold when any entity type is UNKNOWN")
    fuzzy_unknown_type_type_threshold: float = Field(0.50, ge=0, le=1, description="Type threshold when any entity type is UNKNOWN")

    # Weighted scoring components for overall similarity
    name_weight: float = Field(0.50, ge=0, le=1, description="Weight of name similarity in overall score")
    desc_weight: float = Field(0.30, ge=0, le=1, description="Weight of description similarity in overall score")
    type_weight: float = Field(0.20, ge=0, le=1, description="Weight of type similarity in overall score")
    context_bonus: float = Field(0.03, ge=0, le=0.2, description="Bonus added to score when entities co-occur in same statements")

    # Borderline range for LLM fallback triggering
    llm_fallback_floor: float = Field(0.76, ge=0, le=1, description="Lower bound of overall score to consider as borderline for LLM fallback")
    llm_fallback_ceiling: float = Field(0.82, ge=0, le=1, description="Upper bound (below merge threshold) of overall score for LLM fallback")

    # LLM iterative dedup parameters
    llm_block_size: int = Field(50, ge=1, le=500, description="Entities per block for LLM dedup")
    llm_block_concurrency: int = Field(4, ge=1, le=64, description="Concurrent blocks processed by LLM")
    llm_pair_concurrency: int = Field(4, ge=1, le=64, description="Concurrent pairwise decisions per block")
    llm_max_rounds: int = Field(3, ge=1, le=10, description="Maximum LLM iterative dedup rounds")


class ExtractionPipelineConfig(BaseModel):
    """Configuration for the entire extraction pipeline.

    This model combines all configuration components for the complete
    extraction pipeline, including statement extraction, triplet extraction,
    temporal extraction, deduplication, and forgetting mechanisms.

    Attributes:
        statement_extraction: Configuration for statement extraction
        triplet_extraction: Configuration for triplet extraction
        temporal_extraction: Configuration for temporal extraction
        deduplication: Configuration for entity deduplication
        forgetting_engine: Configuration for forgetting engine
    """
    statement_extraction: StatementExtractionConfig = Field(default_factory=StatementExtractionConfig)
    triplet_extraction: TripletExtractionConfig = Field(default_factory=TripletExtractionConfig)
    temporal_extraction: TemporalExtractionConfig = Field(default_factory=TemporalExtractionConfig)
    deduplication: DedupConfig = Field(default_factory=DedupConfig)
    forgetting_engine: ForgettingEngineConfig = Field(default_factory=ForgettingEngineConfig)
