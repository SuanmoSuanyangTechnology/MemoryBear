"""Full read-only mapping for the API-owned ``memory_config`` table.

The table is owned by the API migration chain. This mapping exists so the
memory-config module can read every persisted field with the same names and
types the monolith uses; it deliberately declares no foreign keys or
relationships, and it is never a migration metadata target.

Column defaults mirror the API model. ORM reads never apply a database default;
the columns that carry one are ``NOT NULL`` in the database, so a selected row
always holds the same value the API would have written.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from src.models.base import ReadOnlyBase


class MemoryConfigRow(ReadOnlyBase):
    __tablename__ = "memory_config"

    # Identity
    config_id = Column(UUID(as_uuid=True), primary_key=True)
    config_id_old = Column(Integer, nullable=True)
    config_name = Column(String, nullable=False)
    config_desc = Column(String, nullable=True)

    # Organisation
    workspace_id = Column(UUID(as_uuid=True), nullable=True)
    end_user_id = Column(String, nullable=True)
    user_id = Column(String, nullable=True)
    apply_id = Column(String, nullable=True)

    # Ontology scene
    scene_id = Column(UUID(as_uuid=True), nullable=True)

    # Model selection (inherited from the workspace)
    llm_id = Column(String, nullable=True)
    embedding_id = Column(String, nullable=True)
    rerank_id = Column(String, nullable=True)
    vision_id = Column(String, nullable=True)
    audio_id = Column(String, nullable=True)
    video_id = Column(String, nullable=True)

    # Extraction engine
    enable_llm_dedup_blockwise = Column(Boolean, default=True)
    enable_llm_disambiguation = Column(Boolean, default=True)
    deep_retrieval = Column(Boolean, default=True)
    t_type_strict = Column(Float, default=0.8)
    t_name_strict = Column(Float, default=0.8)
    t_overall = Column(Float, default=0.8)
    state = Column(Boolean, default=False)
    is_default = Column(Boolean, default=False)
    chunker_strategy = Column(String, default="RecursiveChunker")
    pruning_enabled = Column(Boolean, default=True)
    pruning_scene = Column(String, nullable=True)
    pruning_threshold = Column(Float, nullable=True)
    statement_granularity = Column(Integer, default=2)
    include_dialogue_context = Column(Boolean, default=False)
    max_context = Column(Integer, default=1000)

    # Reflection engine
    enable_self_reflexion = Column(Boolean, default=True)
    iteration_period = Column(String, default="3")
    reflexion_range = Column(String, default="partial")
    baseline = Column(String, default="TIME")
    reflection_model_id = Column(String, nullable=True)
    memory_verify = Column(Boolean, default=True)
    quality_assessment = Column(Boolean, default=True)

    # Forgetting engine
    lambda_time = Column(Float, default=0.5)
    lambda_mem = Column(Float, default=0.5)
    offset = Column(Float, default=0.0)
    decay_constant = Column(Float, default=0.5)
    forgetting_threshold = Column(Float, default=0.3)
    forgetting_interval_hours = Column(Integer, default=24)
    enable_llm_summary = Column(Boolean, default=True)
    max_merge_batch_size = Column(Integer, default=100)
    max_history_length = Column(Integer, default=100)
    min_days_since_access = Column(Integer, default=30)

    # SceneSummary boundary
    scene_threshold = Column(Float, nullable=False, default=0.8)
    scene_history_window_size = Column(Integer, nullable=False, default=4)
    scene_min_turns = Column(Integer, nullable=False, default=2)
    scene_max_turns = Column(Integer, nullable=False, default=10)
    scene_idle_timeout_seconds = Column(Integer, nullable=False, default=86400)
    scene_min_chars_to_summary = Column(Integer, nullable=False, default=0)
    time_decay_penalty = Column(Float, nullable=False, default=0.1)

    # SceneCommunity
    batch_trigger_count = Column(Integer, nullable=False, default=20)
    candidate_community_limit = Column(Integer, nullable=False, default=3)
    compare_all_same_category_communities = Column(Boolean, nullable=False, default=False)
    rebuild_new_scene_community_count_enabled = Column(Boolean, nullable=False, default=True)
    rebuild_new_scene_community_count = Column(Integer, nullable=False, default=50)
    rebuild_interval_enabled = Column(Boolean, nullable=False, default=True)
    rebuild_interval_days = Column(Integer, nullable=False, default=1)

    # Emotion engine
    emotion_enabled = Column(Boolean, default=True)
    emotion_model_id = Column(String, nullable=True)
    emotion_extract_keywords = Column(Boolean, default=True)
    emotion_min_intensity = Column(Float, default=0.1)
    emotion_enable_subject = Column(Boolean, default=True)

    # Prediction engine
    prediction_candidate_limit = Column(Integer, nullable=False, default=8)
    prediction_participant_limit = Column(Integer, nullable=False, default=3)
    prediction_max_steps = Column(Integer, nullable=False, default=4)
    prediction_recall_limit = Column(Integer, nullable=False, default=20)
    prediction_min_valid_memory_count = Column(Integer, nullable=False, default=3)
    prediction_embedding_min_similarity = Column(Float, nullable=False, default=0.7)

    # Coding Agent preference engine
    preference_engine_enabled = Column(Boolean, nullable=False, default=False)
    preference_custom_keywords = Column(JSONB, nullable=False, default=list)

    # Personality anchor engine
    personality_anchor_enabled = Column(Boolean, nullable=False, default=False)
    personality_anchor_auto_solidify = Column(Boolean, nullable=False, default=False)
    personality_anchor_read_metadata = Column(Boolean, nullable=False, default=False)
    personality_anchor_read_preferences = Column(Boolean, nullable=False, default=False)
    personality_anchor_sync_cycle = Column(String(16), nullable=False, default="daily")
    personality_anchor_base_principles = Column(Text, nullable=False, default="")

    created_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=True)
