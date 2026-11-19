from __future__ import annotations

from src.common.datetime_utils import utcnow_naive
from src.schemas.memory_config import MemoryConfig


def get_pipeline_config(memory_config: MemoryConfig):
    """Build ExtractionPipelineConfig from MemoryConfig.

    Args:
        memory_config: MemoryConfig object containing all pipeline settings.

    Returns:
        ExtractionPipelineConfig with deduplication, statement extraction,
        and forgetting engine settings.
    """
    from src.schemas.extraction_engine.variate_config import (
        DedupConfig,
        ExtractionPipelineConfig,
        ForgettingEngineConfig,
        StatementExtractionConfig,
    )

    dedup_config = DedupConfig(
        enable_llm_dedup_blockwise=memory_config.enable_llm_dedup_blockwise,
        enable_llm_disambiguation=memory_config.enable_llm_disambiguation,
        fuzzy_name_threshold_strict=memory_config.t_name_strict,
        fuzzy_type_threshold_strict=memory_config.t_type_strict,
        fuzzy_overall_threshold=memory_config.t_overall,
    )

    stmt_config = StatementExtractionConfig(
        statement_granularity=memory_config.statement_granularity,
        include_dialogue_context=memory_config.include_dialogue_context,
        max_dialogue_context_chars=memory_config.max_dialogue_context_chars,
    )

    forget_config = ForgettingEngineConfig(
        offset=memory_config.offset,
        lambda_time=memory_config.lambda_time,
        lambda_mem=memory_config.lambda_mem,
    )

    return ExtractionPipelineConfig(
        statement_extraction=stmt_config,
        deduplication=dedup_config,
        forgetting_engine=forget_config,
        emotion_enabled=getattr(memory_config, "emotion_enabled", False),
    )


def get_pruning_config(memory_config: MemoryConfig) -> dict:
    """Retrieve semantic pruning config from MemoryConfig.

    Args:
        memory_config: MemoryConfig object containing pruning settings.

    Returns:
        Dict suitable for PruningConfig.model_validate with keys:
        - pruning_switch: bool
        - pruning_threshold: float
    """
    return {
        "pruning_switch": memory_config.pruning_enabled,
        "pruning_threshold": memory_config.pruning_threshold,
    }




def _build_memory_config(
    memory_config_row,
    workspace,
    llm_uuid,
    llm_name,
    embedding_uuid,
    embedding_name,
    rerank_uuid,
    rerank_name,
    vision_uuid,
    vision_name,
    audio_uuid,
    audio_name,
    video_uuid,
    video_name,
) -> MemoryConfig:
    """Construct a MemoryConfig from validated models — shared by sync and async paths."""
    return MemoryConfig(
        config_id=memory_config_row.config_id,
        config_name=memory_config_row.config_name,
        workspace_id=workspace.id,
        workspace_name=workspace.name,
        tenant_id=workspace.tenant_id,
        llm_model_id=llm_uuid,
        llm_model_name=llm_name,
        embedding_model_id=embedding_uuid,
        embedding_model_name=embedding_name,
        rerank_model_id=rerank_uuid,
        rerank_model_name=rerank_name,
        video_model_id=video_uuid,
        video_model_name=video_name,
        vision_model_id=vision_uuid,
        vision_model_name=vision_name,
        audio_model_id=audio_uuid,
        audio_model_name=audio_name,
        storage_type=workspace.storage_type or "neo4j",
        chunker_strategy=memory_config_row.chunker_strategy or "RecursiveChunker",
        reflexion_enabled=memory_config_row.enable_self_reflexion or False,
        reflexion_iteration_period=int(memory_config_row.iteration_period or "3"),
        reflexion_range=memory_config_row.reflexion_range or "partial",
        reflexion_baseline=memory_config_row.baseline or "Time",
        loaded_at=utcnow_naive(),
        # Pipeline config: Deduplication
        enable_llm_dedup_blockwise=bool(memory_config_row.enable_llm_dedup_blockwise)
        if memory_config_row.enable_llm_dedup_blockwise is not None
        else False,
        enable_llm_disambiguation=bool(memory_config_row.enable_llm_disambiguation)
        if memory_config_row.enable_llm_disambiguation is not None
        else False,
        deep_retrieval=bool(memory_config_row.deep_retrieval)
        if memory_config_row.deep_retrieval is not None
        else True,
        t_type_strict=float(memory_config_row.t_type_strict)
        if memory_config_row.t_type_strict is not None
        else 0.8,
        t_name_strict=float(memory_config_row.t_name_strict)
        if memory_config_row.t_name_strict is not None
        else 0.8,
        t_overall=float(memory_config_row.t_overall)
        if memory_config_row.t_overall is not None
        else 0.8,
        # Pipeline config: Statement extraction
        statement_granularity=int(memory_config_row.statement_granularity)
        if memory_config_row.statement_granularity is not None
        else 2,
        include_dialogue_context=bool(memory_config_row.include_dialogue_context)
        if memory_config_row.include_dialogue_context is not None
        else False,
        max_dialogue_context_chars=int(memory_config_row.max_context)
        if memory_config_row.max_context is not None
        else 1000,
        # Pipeline config: Forgetting engine
        lambda_time=float(memory_config_row.lambda_time)
        if memory_config_row.lambda_time is not None
        else 0.5,
        lambda_mem=float(memory_config_row.lambda_mem)
        if memory_config_row.lambda_mem is not None
        else 0.5,
        offset=float(memory_config_row.offset) if memory_config_row.offset is not None else 0.0,
        # Pipeline config: Pruning
        pruning_enabled=bool(memory_config_row.pruning_enabled)
        if memory_config_row.pruning_enabled is not None
        else False,
        pruning_threshold=float(memory_config_row.pruning_threshold)
        if memory_config_row.pruning_threshold is not None
        else 0.5,
        # Pipeline config: Scene boundary and SceneSummary
        scene_threshold=float(
            memory_config_row.scene_threshold
            if memory_config_row.scene_threshold is not None
            else 0.8
        ),
        scene_history_window_size=int(
            memory_config_row.scene_history_window_size
            if memory_config_row.scene_history_window_size is not None
            else 4
        ),
        scene_min_turns=int(
            memory_config_row.scene_min_turns
            if memory_config_row.scene_min_turns is not None
            else 2
        ),
        scene_max_turns=int(
            memory_config_row.scene_max_turns
            if memory_config_row.scene_max_turns is not None
            else 10
        ),
        scene_idle_timeout_seconds=int(
            memory_config_row.scene_idle_timeout_seconds
            if memory_config_row.scene_idle_timeout_seconds is not None
            else 86400
        ),
        scene_min_chars_to_summary=int(
            memory_config_row.scene_min_chars_to_summary
            if memory_config_row.scene_min_chars_to_summary is not None
            else 0
        ),
        time_decay_penalty=float(
            memory_config_row.time_decay_penalty
            if memory_config_row.time_decay_penalty is not None
            else 0.1
        ),
        # Pipeline config: SceneCommunity
        batch_trigger_count=int(
            memory_config_row.batch_trigger_count
            if memory_config_row.batch_trigger_count is not None
            else 20
        ),
        candidate_community_limit=int(
            memory_config_row.candidate_community_limit
            if memory_config_row.candidate_community_limit is not None
            else 3
        ),
        compare_all_same_category_communities=bool(
            memory_config_row.compare_all_same_category_communities
        ),
        rebuild_new_scene_community_count_enabled=(
            bool(memory_config_row.rebuild_new_scene_community_count_enabled)
            if memory_config_row.rebuild_new_scene_community_count_enabled is not None
            else True
        ),
        rebuild_new_scene_community_count=int(
            memory_config_row.rebuild_new_scene_community_count
            if memory_config_row.rebuild_new_scene_community_count is not None
            else 50
        ),
        rebuild_interval_enabled=(
            bool(memory_config_row.rebuild_interval_enabled)
            if memory_config_row.rebuild_interval_enabled is not None
            else True
        ),
        rebuild_interval_days=int(
            memory_config_row.rebuild_interval_days
            if memory_config_row.rebuild_interval_days is not None
            else 1
        ),
        # Pipeline config: Emotion extraction
        emotion_enabled=bool(memory_config_row.emotion_enabled)
        if memory_config_row.emotion_enabled is not None
        else False,
        # Prediction engine
        prediction_candidate_limit=int(memory_config_row.prediction_candidate_limit),
        prediction_participant_limit=int(memory_config_row.prediction_participant_limit),
        prediction_max_steps=int(memory_config_row.prediction_max_steps),
        prediction_recall_limit=int(memory_config_row.prediction_recall_limit),
        prediction_min_valid_memory_count=int(memory_config_row.prediction_min_valid_memory_count),
        prediction_embedding_min_similarity=float(
            memory_config_row.prediction_embedding_min_similarity
        ),
        # Pipeline config: Coding Agent preference extension
        preference_engine_enabled=bool(
            getattr(memory_config_row, "preference_engine_enabled", False)
        ),
        preference_custom_keywords=tuple(
            getattr(memory_config_row, "preference_custom_keywords", None) or []
        ),
    )
