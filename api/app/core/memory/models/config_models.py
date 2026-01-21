"""Configuration models for Memory module components.

This module contains Pydantic models for configuring various components
of the memory system including LLM, chunking, pruning, and search.

Classes:
    LLMConfig: Configuration for LLM client
    ChunkerConfig: Configuration for dialogue chunking
    PruningConfig: Configuration for semantic pruning
    TemporalSearchParams: Parameters for temporal search queries
"""

from typing import Optional
from pydantic import BaseModel, Field


class LLMConfig(BaseModel):
    """Configuration for Large Language Model client.

    Attributes:
        llm_name: The name of the LLM model to use (e.g., 'gpt-4', 'claude-3')
        api_base: Optional base URL for the API endpoint
        max_retries: Maximum number of retries for failed API calls (default: 3)
    """
    llm_name: str = Field(..., description="The name of the LLM model to use.")
    api_base: Optional[str] = Field(None, description="The base URL for the API endpoint.")
    max_retries: Optional[int] = Field(3, ge=0, description="The maximum number of retries for API calls.")


class ChunkerConfig(BaseModel):
    """Configuration for dialogue chunking strategy.

    Attributes:
        chunker_strategy: Name of the chunking strategy (e.g., 'RecursiveChunker', 'SemanticChunker')
        embedding_model: Name of the embedding model to use for semantic chunking
        chunk_size: Maximum size of each chunk in characters (default: 2048)
        threshold: Similarity threshold for semantic chunking (0-1, default: 0.8)
        language: Language of the text (default: 'zh' for Chinese)
        skip_window: Window size for skip-and-merge strategy (default: 0)
        min_sentences: Minimum number of sentences per chunk (default: 1)
        min_characters_per_chunk: Minimum characters per chunk (default: 24)
    """
    chunker_strategy: str = Field(..., description="The name of the chunker strategy to use.")
    embedding_model: str = Field(..., description="The name of the embedding model to use.")
    chunk_size: Optional[int] = Field(2048, ge=0, description="The size of each chunk.")
    threshold: Optional[float] = Field(0.8, ge=0, le=1, description="The threshold for similarity.")
    language: Optional[str] = Field("zh", description="The language of the text.")
    skip_window: Optional[int] = Field(0, ge=0, description="The window for skip-and-merge.")
    min_sentences: Optional[int] = Field(1, ge=0, description="The minimum number of sentences in each chunk.")
    min_characters_per_chunk: Optional[int] = Field(24, ge=0, description="The minimum number of characters in each chunk.")


class PruningConfig(BaseModel):
    """Configuration for semantic pruning of dialogue content.

    Attributes:
        pruning_switch: Enable or disable semantic pruning
        pruning_scene: Scene type for pruning ('education', 'online_service', 'outbound')
        pruning_threshold: Pruning ratio (0-0.9, max 0.9 to avoid complete removal)
    """
    pruning_switch: bool = Field(False, description="Enable semantic pruning when True.")
    pruning_scene: str = Field(
        "education",
        description="Scene for pruning: one of 'education', 'online_service', 'outbound'.",
    )
    pruning_threshold: float = Field(
        0.5, ge=0.0, le=0.9,
        description="Pruning ratio within 0-0.9 (max 0.9 to avoid termination).")


class TemporalSearchParams(BaseModel):
    """Parameters for temporal search queries in the knowledge graph.

    Attributes:
        end_user_id: Group ID to filter search results (default: 'test')
        apply_id: Application ID to filter search results
        user_id: User ID to filter search results
        start_date: Start date for temporal filtering (format: 'YYYY-MM-DD')
        end_date: End date for temporal filtering (format: 'YYYY-MM-DD')
        valid_date: Date when memory should be valid (format: 'YYYY-MM-DD')
        invalid_date: Date when memory should be invalid (format: 'YYYY-MM-DD')
        limit: Maximum number of results to return (default: 3)
    """
    end_user_id: Optional[str] = Field("test", description="The group ID to filter the search.")
    apply_id: Optional[str] = Field(None, description="The apply ID to filter the search.")
    user_id: Optional[str] = Field(None, description="The user ID to filter the search.")
    start_date: Optional[str] = Field(None, description="The start date for the search.")
    end_date: Optional[str] = Field(None, description="The end date for the search.")
    valid_date: Optional[str] = Field(None, description="The valid date for the search.")
    invalid_date: Optional[str] = Field(None, description="The invalid date for the search.")
    limit: int = Field(default=3, description="The maximum number of results to return.")


