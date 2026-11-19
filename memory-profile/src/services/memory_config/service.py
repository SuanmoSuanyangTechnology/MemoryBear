from __future__ import annotations

from types import SimpleNamespace
from typing import Optional, Union
from uuid import UUID

from src.schemas.memory_config import InvalidConfigError, ModelInactiveError, ModelNotFoundError

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
    from src.models.variate_config import (
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
        llm_uuid, llm_name,
        embedding_uuid, embedding_name,
        rerank_uuid, rerank_name,
        vision_uuid, vision_name,
        audio_uuid, audio_name,
        video_uuid, video_name,
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
        enable_llm_dedup_blockwise=bool(
            memory_config_row.enable_llm_dedup_blockwise) if memory_config_row.enable_llm_dedup_blockwise is not None else False,
        enable_llm_disambiguation=bool(
            memory_config_row.enable_llm_disambiguation) if memory_config_row.enable_llm_disambiguation is not None else False,
        deep_retrieval=bool(
            memory_config_row.deep_retrieval) if memory_config_row.deep_retrieval is not None else True,
        t_type_strict=float(
            memory_config_row.t_type_strict) if memory_config_row.t_type_strict is not None else 0.8,
        t_name_strict=float(
            memory_config_row.t_name_strict) if memory_config_row.t_name_strict is not None else 0.8,
        t_overall=float(
            memory_config_row.t_overall) if memory_config_row.t_overall is not None else 0.8,
        # Pipeline config: Statement extraction
        statement_granularity=int(
            memory_config_row.statement_granularity) if memory_config_row.statement_granularity is not None else 2,
        include_dialogue_context=bool(
            memory_config_row.include_dialogue_context) if memory_config_row.include_dialogue_context is not None else False,
        max_dialogue_context_chars=int(
            memory_config_row.max_context) if memory_config_row.max_context is not None else 1000,
        # Pipeline config: Forgetting engine
        lambda_time=float(
            memory_config_row.lambda_time) if memory_config_row.lambda_time is not None else 0.5,
        lambda_mem=float(
            memory_config_row.lambda_mem) if memory_config_row.lambda_mem is not None else 0.5,
        offset=float(memory_config_row.offset) if memory_config_row.offset is not None else 0.0,
        # Pipeline config: Pruning
        pruning_enabled=bool(
            memory_config_row.pruning_enabled) if memory_config_row.pruning_enabled is not None else False,
        pruning_threshold=float(
            memory_config_row.pruning_threshold) if memory_config_row.pruning_threshold is not None else 0.5,
        # Pipeline config: Scene boundary and SceneSummary
        scene_threshold=float(memory_config_row.scene_threshold if memory_config_row.scene_threshold is not None else 0.8),
        scene_history_window_size=int(memory_config_row.scene_history_window_size if memory_config_row.scene_history_window_size is not None else 4),
        scene_min_turns=int(memory_config_row.scene_min_turns if memory_config_row.scene_min_turns is not None else 2),
        scene_max_turns=int(memory_config_row.scene_max_turns if memory_config_row.scene_max_turns is not None else 10),
        scene_idle_timeout_seconds=int(memory_config_row.scene_idle_timeout_seconds if memory_config_row.scene_idle_timeout_seconds is not None else 86400),
        scene_min_chars_to_summary=int(memory_config_row.scene_min_chars_to_summary if memory_config_row.scene_min_chars_to_summary is not None else 0),
        time_decay_penalty=float(memory_config_row.time_decay_penalty if memory_config_row.time_decay_penalty is not None else 0.1),
        # Pipeline config: SceneCommunity
        batch_trigger_count=int(memory_config_row.batch_trigger_count if memory_config_row.batch_trigger_count is not None else 20),
        candidate_community_limit=int(memory_config_row.candidate_community_limit if memory_config_row.candidate_community_limit is not None else 3),
        compare_all_same_category_communities=bool(memory_config_row.compare_all_same_category_communities),
        rebuild_new_scene_community_count_enabled=(
            bool(memory_config_row.rebuild_new_scene_community_count_enabled)
            if memory_config_row.rebuild_new_scene_community_count_enabled is not None
            else True
        ),
        rebuild_new_scene_community_count=int(memory_config_row.rebuild_new_scene_community_count if memory_config_row.rebuild_new_scene_community_count is not None else 50),
        rebuild_interval_enabled=(
            bool(memory_config_row.rebuild_interval_enabled)
            if memory_config_row.rebuild_interval_enabled is not None
            else True
        ),
        rebuild_interval_days=int(memory_config_row.rebuild_interval_days if memory_config_row.rebuild_interval_days is not None else 1),
        # Pipeline config: Emotion extraction
        emotion_enabled=bool(
            memory_config_row.emotion_enabled) if memory_config_row.emotion_enabled is not None else False,
        # Prediction engine
        prediction_candidate_limit=int(memory_config_row.prediction_candidate_limit),
        prediction_participant_limit=int(memory_config_row.prediction_participant_limit),
        prediction_max_steps=int(memory_config_row.prediction_max_steps),
        prediction_recall_limit=int(memory_config_row.prediction_recall_limit),
        prediction_min_valid_memory_count=int(memory_config_row.prediction_min_valid_memory_count),
        prediction_embedding_min_similarity=float(memory_config_row.prediction_embedding_min_similarity),
        # Pipeline config: Coding Agent preference extension
        preference_engine_enabled=bool(
            getattr(memory_config_row, "preference_engine_enabled", False)
        ),
        preference_custom_keywords=tuple(
            getattr(memory_config_row, "preference_custom_keywords", None) or []
        ),
    )


async def load_memory_config_async(runtime, config_id, workspace_id, tenant_id, *, include_runtime_views=True):
    from types import SimpleNamespace
    from uuid import UUID

    from src.schemas.memory_config import ConfigurationError
    from src.schemas.memory_storage import ModelInfo

    from src.repositories.memory_config import MemoryConfigReadRepository
    from src.repositories.model_config import ModelConfigRepository
    from src.repositories.workspace import WorkspaceRepository

    configs = MemoryConfigReadRepository(runtime.postgres)
    config_id = await configs.resolve_trial_config_id(config_id, workspace_id)
    row = await configs.get_owned(config_id, workspace_id) if config_id else None
    workspace = await WorkspaceRepository(runtime.postgres).workspace_row(workspace_id)
    if row is None or workspace is None or workspace["tenant_id"] != tenant_id:
        raise ConfigurationError(
            f"Configuration not found: config_id={config_id}", config_id=config_id, workspace_id=workspace_id
        )
    models = ModelConfigRepository(runtime.postgres)
    preset = await models.default_model_preset() if workspace["is_default_config"] else None
    effective_models = _effective_workspace_models(
        SimpleNamespace(**workspace), SimpleNamespace(**preset) if preset else None
    )
    model_results = await _validate_models_batch_async(
        models, effective_models, tenant_id, config_id, workspace_id
    )
    views = await models.trial_views(
        [value[0] for value in model_results.values() if value[0] is not None], tenant_id
    ) if include_runtime_views else {}
    arguments = {}
    for slot, (model_id, model_name) in model_results.items():
        arguments[slot + "_uuid"] = model_id
        arguments[slot + "_name"] = model_name
    config = _build_memory_config(
        SimpleNamespace(**row), SimpleNamespace(**workspace), **arguments
    )
    model_views = {
        key: ModelInfo(
            model_name=value["name"],
            provider=value["provider"],
            model_type=value["type"],
            model_config_id=str(key),
            tenant_id=str(tenant_id),
            input_modalities=value["input_modalities"],
            output_modalities=value["output_modalities"],
            features=value["features"],
        )
        for key, value in views.items()
    }
    return config, model_views


def _effective_workspace_models(workspace, preset) -> dict:
    """Return the model IDs a workspace should resolve at runtime.

    Default-config workspaces follow the singleton ``WorkspaceDefaultModelPreset``
    (their own columns are only a copy). Other workspaces use their own columns.
    Falls back to the workspace's own columns when the preset is missing.
    """
    if workspace.is_default_config and preset is not None:
        return {
            "llm": preset.llm_model_config_id,
            "embedding": preset.embedding_model_config_id,
            "rerank": preset.rerank_model_config_id,
            "vision": preset.vision_model_config_id,
            "audio": preset.audio_model_config_id,
            "video": preset.video_model_config_id,
        }
    return {
        "llm": workspace.llm,
        "embedding": workspace.embedding,
        "rerank": workspace.rerank,
        "vision": workspace.vision,
        "audio": workspace.audio,
        "video": workspace.video,
    }

def _parse_model_id(model_id: Union[str, UUID, None], model_type: str,
                    config_id: Optional[UUID] = None, workspace_id: Optional[UUID] = None) -> Optional[UUID]:
    """Parse model ID from string or UUID."""
    if model_id is None:
        return None
    if isinstance(model_id, UUID):
        return model_id
    if isinstance(model_id, str):
        if not model_id.strip():
            return None
        try:
            return UUID(model_id.strip())
        except ValueError:
            raise InvalidConfigError(
                f"Invalid UUID format for {model_type} model ID: '{model_id}'",
                field_name=f"{model_type}_model_id",
                invalid_value=model_id,
                config_id=config_id,
                workspace_id=workspace_id
            )
    raise InvalidConfigError(
        f"Invalid type for {model_type} model ID: expected str or UUID, got {type(model_id).__name__}",
        field_name=f"{model_type}_model_id",
        invalid_value=model_id,
        config_id=config_id,
        workspace_id=workspace_id
    )

_MODEL_VALIDATION_SPECS = (
    ("embedding", "embedding", True),
    ("llm", "llm", True),
    ("rerank", "rerank", False),
    ("vision", "llm", False),
    ("audio", "llm", False),
    ("video", "llm", False),
)

async def _validate_models_batch_async(
    repository,
    models: dict,
    tenant_id: Optional[UUID],
    config_id: UUID,
    workspace_id: UUID,
) -> dict:
    parsed: dict[str, Optional[UUID]] = {}
    for key, model_type, required in _MODEL_VALIDATION_SPECS:
        model_id_str = models[key]
        if model_id_str is None or (isinstance(model_id_str, str) and not model_id_str.strip()):
            if required:
                raise InvalidConfigError(
                    f"{model_type.title()} model ID is required",
                    field_name=f"{model_type}_model_id",
                    invalid_value=model_id_str,
                    config_id=config_id,
                    workspace_id=workspace_id,
                )
            parsed[key] = None
        else:
            parsed[key] = _parse_model_id(model_id_str, model_type, config_id, workspace_id)

    ids_to_query = list(dict.fromkeys(u for u in parsed.values() if u is not None))
    found = {
        str(key): SimpleNamespace(**value)
        for key, value in (await repository.get_models(ids_to_query, tenant_id)).items()
    }
    missed = [u for u in ids_to_query if str(u) not in found]
    found_without_tenant = {
        str(key): SimpleNamespace(**value)
        for key, value in (await repository.get_models(missed)).items()
    } if missed else {}

    out: dict[str, tuple[Optional[UUID], Optional[str]]] = {}
    for key, model_type, _required in _MODEL_VALIDATION_SPECS:
        model_uuid = parsed[key]
        if model_uuid is None:
            out[key] = (None, None)
            continue

        model = found.get(str(model_uuid))
        if model is None:
            model_without_tenant = found_without_tenant.get(str(model_uuid))
            if model_without_tenant is not None:
                raise ModelNotFoundError(
                    model_id=model_uuid,
                    model_type=model_type,
                    config_id=config_id,
                    workspace_id=workspace_id,
                    message=(
                        f"{model_type.title()} model {model_uuid} "
                        f"({model_without_tenant.name}) belongs to a different tenant"
                    ),
                )
            raise ModelNotFoundError(
                model_id=model_uuid,
                model_type=model_type,
                config_id=config_id,
                workspace_id=workspace_id,
                message=f"{model_type.title()} model {model_uuid} not found",
            )

        if not model.is_active:
            raise ModelInactiveError(
                model_id=model_uuid,
                model_name=model.name,
                model_type=model_type,
                config_id=config_id,
                workspace_id=workspace_id,
                message=f"{model_type.title()} model {model_uuid} ({model.name}) is inactive",
            )
        out[key] = (model_uuid, model.name)

    return out
