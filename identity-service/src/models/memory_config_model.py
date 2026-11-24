"""Write mirror of ``memory_config`` (columns mirror the monolith minus FKs/checks)."""
from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from src.models.base import SharedBase, utcnow_naive


class MemoryConfig(SharedBase):
    __tablename__ = "memory_config"

    config_id = Column(UUID(as_uuid=True), primary_key=True)
    config_id_old = Column(Integer, nullable=True)
    config_name = Column(String, nullable=False)
    config_desc = Column(String, nullable=True)

    workspace_id = Column(UUID(as_uuid=True), nullable=True)
    end_user_id = Column(String, nullable=True)
    user_id = Column(String, nullable=True)
    apply_id = Column(String, nullable=True)

    scene_id = Column(UUID(as_uuid=True), nullable=True)

    llm_id = Column(String, nullable=True)
    embedding_id = Column(String, nullable=True)
    rerank_id = Column(String, nullable=True)
    vision_id = Column(String, nullable=True)
    audio_id = Column(String, nullable=True)
    video_id = Column(String, nullable=True)

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

    enable_self_reflexion = Column(Boolean, default=True)
    iteration_period = Column(String, default="3")
    reflexion_range = Column(String, default="partial")
    baseline = Column(String, default="TIME")
    reflection_model_id = Column(String, nullable=True)
    memory_verify = Column(Boolean, default=True)
    quality_assessment = Column(Boolean, default=True)

    statement_granularity = Column(Integer, default=2)
    include_dialogue_context = Column(Boolean, default=False)
    max_context = Column(Integer, default=1000)
    lambda_time = Column("lambda_time", Float, default=0.5)
    lambda_mem = Column("lambda_mem", Float, default=0.5)
    offset = Column("offset", Float, default=0.0)

    decay_constant = Column(Float, default=0.5)
    forgetting_threshold = Column(Float, default=0.3)
    forgetting_interval_hours = Column(Integer, default=24)
    enable_llm_summary = Column(Boolean, default=True)
    max_merge_batch_size = Column(Integer, default=100)
    max_history_length = Column(Integer, default=100)
    min_days_since_access = Column(Integer, default=30)

    scene_threshold = Column(Float, nullable=False, default=0.8, server_default="0.8")
    scene_history_window_size = Column(Integer, nullable=False, default=4, server_default="4")
    scene_min_turns = Column(Integer, nullable=False, default=2, server_default="2")
    scene_max_turns = Column(Integer, nullable=False, default=10, server_default="10")
    scene_idle_timeout_seconds = Column(
        Integer, nullable=False, default=86400, server_default="86400"
    )
    scene_min_chars_to_summary = Column(Integer, nullable=False, default=0, server_default="0")
    time_decay_penalty = Column(Float, nullable=False, default=0.1, server_default="0.1")

    batch_trigger_count = Column(Integer, nullable=False, default=20, server_default="20")
    candidate_community_limit = Column(Integer, nullable=False, default=3, server_default="3")
    compare_all_same_category_communities = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    rebuild_new_scene_community_count_enabled = Column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    rebuild_new_scene_community_count = Column(
        Integer, nullable=False, default=50, server_default="50"
    )
    rebuild_interval_enabled = Column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    rebuild_interval_days = Column(Integer, nullable=False, default=1, server_default="1")

    emotion_enabled = Column(Boolean, default=True)
    emotion_model_id = Column(String, nullable=True)
    emotion_extract_keywords = Column(Boolean, default=True)
    emotion_min_intensity = Column(Float, default=0.1)
    emotion_enable_subject = Column(Boolean, default=True)

    prediction_candidate_limit = Column(Integer, nullable=False, default=8, server_default="8")
    prediction_participant_limit = Column(Integer, nullable=False, default=3, server_default="3")
    prediction_max_steps = Column(Integer, nullable=False, default=4, server_default="4")
    prediction_recall_limit = Column(Integer, nullable=False, default=20, server_default="20")
    prediction_min_valid_memory_count = Column(
        Integer, nullable=False, default=3, server_default="3"
    )
    prediction_embedding_min_similarity = Column(
        Float, nullable=False, default=0.7, server_default="0.7"
    )
    preference_engine_enabled = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    preference_custom_keywords = Column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )

    personality_anchor_enabled = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    personality_anchor_auto_solidify = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    personality_anchor_read_metadata = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    personality_anchor_read_preferences = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    personality_anchor_sync_cycle = Column(
        String(16), nullable=False, default="daily", server_default="daily"
    )
    personality_anchor_base_principles = Column(
        Text, nullable=False, default="", server_default=""
    )

    created_at = Column(DateTime, default=utcnow_naive)
    updated_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)
