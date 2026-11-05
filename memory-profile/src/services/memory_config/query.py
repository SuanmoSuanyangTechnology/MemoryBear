"""Read use cases for the memory-configuration module.

Each method returns the exact payload the monolith's handler returned, or
raises one of the typed failures in :mod:`src.services.memory_config.errors`.
Serialization rules that the legacy code applied implicitly — emotion and
reflection read their model id from the workspace, the forgetting rate is
``lambda_time / lambda_mem`` — are reproduced here rather than in the transport
layer.

Every endpoint that does not persist data lives here, including the list view
and the preference sub-resource; writes stay in :mod:`mutation`.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from memory_contracts.preferences import (
    DEFAULT_PREFERENCE_KEYWORDS,
    effective_keywords,
)

from src.infrastructure.postgres import PostgresManager
from src.repositories.memory_config import (
    EMOTION_COLUMNS,
    EXTRACTED_COLUMNS,
    FORGETTING_COLUMNS,
    PERSONALITY_COLUMNS,
    PREDICTION_COLUMNS,
    PREFERENCE_COLUMNS,
    REFLECTION_COLUMNS,
    SCENE_COLUMNS,
    SCENE_COMMUNITY_COLUMNS,
    WORKSPACE_LLM_COLUMN,
    WORKSPACE_MODEL_COLUMNS,
    MemoryConfigReadRepository,
)
from src.repositories.workspace import WorkspaceRepository
from src.schemas.memory_config import (
    EmotionConfigData,
    ExtractedConfigData,
    ForgettingConfigData,
    PersonalityAnchorData,
    PredictionConfigData,
    PreferenceConfigData,
    ReflectionConfigData,
    SceneCommunityConfigData,
    SceneConfigData,
)
from src.services.memory_config.errors import (
    ConfigQueryError,
    ConfigValidationError,
)
from src.services.memory_config.ownership import require_owned_config

_PERSONALITY_FIELDS = (
    "personality_anchor_enabled",
    "personality_anchor_auto_solidify",
    "personality_anchor_read_metadata",
    "personality_anchor_read_preferences",
    "personality_anchor_sync_cycle",
    "personality_anchor_base_principles",
)
_PREDICTION_FIELDS = (
    "prediction_candidate_limit",
    "prediction_participant_limit",
    "prediction_max_steps",
    "prediction_recall_limit",
    "prediction_min_valid_memory_count",
    "prediction_embedding_min_similarity",
)


def _to_timestamp_ms(value: datetime | None) -> int | None:
    """Serialize a datetime to a UTC millisecond timestamp (API convention)."""

    if value is None:
        return None
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return int(aware.timestamp() * 1000)


class MemoryConfigQueryService:
    def __init__(self, postgres: PostgresManager) -> None:
        self.repository = MemoryConfigReadRepository(postgres)
        self.workspaces = WorkspaceRepository(postgres)

    async def personality_anchor(
        self, config_id: UUID | str, workspace_id: UUID
    ) -> PersonalityAnchorData:
        row = await require_owned_config(
            self.repository, config_id, workspace_id, columns=PERSONALITY_COLUMNS
        )
        return PersonalityAnchorData(
            config_id=row["config_id"],
            is_default=bool(row["is_default"]),
            **{field: row[field] for field in _PERSONALITY_FIELDS},
        )

    async def extracted(self, config_id: UUID | str, workspace_id: UUID) -> ExtractedConfigData:
        row = await require_owned_config(
            self.repository,
            config_id,
            workspace_id,
            columns=EXTRACTED_COLUMNS,
            workspace_columns=WORKSPACE_MODEL_COLUMNS,
        )
        return ExtractedConfigData(
            llm_id=row["llm"],
            embedding_id=row["embedding"],
            rerank_id=row["rerank"],
            vision_id=row["vision"],
            audio_id=row["audio"],
            video_id=row["video"],
            enable_llm_dedup_blockwise=row["enable_llm_dedup_blockwise"],
            enable_llm_disambiguation=row["enable_llm_disambiguation"],
            deep_retrieval=row["deep_retrieval"],
            t_type_strict=row["t_type_strict"],
            t_name_strict=row["t_name_strict"],
            t_overall=row["t_overall"],
            chunker_strategy=row["chunker_strategy"],
            statement_granularity=row["statement_granularity"],
            include_dialogue_context=row["include_dialogue_context"],
            max_context=row["max_context"],
            pruning_enabled=row["pruning_enabled"],
            pruning_scene=row["pruning_scene"],
            pruning_threshold=row["pruning_threshold"],
            enable_self_reflexion=row["enable_self_reflexion"],
            iteration_period=row["iteration_period"],
            reflexion_range=row["reflexion_range"],
            baseline=row["baseline"],
            is_default=bool(row["is_default"]),
        )

    async def forgetting(self, config_id: UUID | str, workspace_id: UUID) -> ForgettingConfigData:
        row = await require_owned_config(
            self.repository, config_id, workspace_id, columns=FORGETTING_COLUMNS
        )
        lambda_time = row["lambda_time"]
        lambda_mem = row["lambda_mem"]
        if not lambda_mem:
            raise ConfigQueryError("lambda_mem 不能为 0")
        return ForgettingConfigData(
            config_id=row["config_id"],
            decay_constant=row["decay_constant"],
            lambda_time=lambda_time,
            lambda_mem=lambda_mem,
            forgetting_rate=lambda_time / lambda_mem,
            offset=row["offset"],
            max_history_length=row["max_history_length"],
            forgetting_threshold=row["forgetting_threshold"],
            min_days_since_access=row["min_days_since_access"],
            enable_llm_summary=row["enable_llm_summary"],
            max_merge_batch_size=row["max_merge_batch_size"],
            forgetting_interval_hours=row["forgetting_interval_hours"],
            is_default=bool(row["is_default"]),
        )

    async def emotion(self, config_id: UUID | str, workspace_id: UUID) -> EmotionConfigData:
        row = await require_owned_config(
            self.repository,
            config_id,
            workspace_id,
            columns=EMOTION_COLUMNS,
            workspace_columns=WORKSPACE_LLM_COLUMN,
        )
        return EmotionConfigData(
            config_id=row["config_id"],
            emotion_enabled=row["emotion_enabled"],
            # The contract reads the workspace's LLM, not ``emotion_model_id``.
            emotion_model_id=row["llm"],
            emotion_extract_keywords=row["emotion_extract_keywords"],
            emotion_min_intensity=row["emotion_min_intensity"],
            emotion_enable_subject=row["emotion_enable_subject"],
            is_default=bool(row["is_default"]),
        )

    async def reflection(self, config_id: UUID | str, workspace_id: UUID) -> ReflectionConfigData:
        row = await require_owned_config(
            self.repository,
            config_id,
            workspace_id,
            columns=REFLECTION_COLUMNS,
            workspace_columns=WORKSPACE_LLM_COLUMN,
        )
        return ReflectionConfigData(
            config_id=row["config_id"],
            reflection_enabled=row["enable_self_reflexion"],
            reflection_period_in_hours=row["iteration_period"],
            reflexion_range=row["reflexion_range"],
            baseline=row["baseline"],
            reflection_model_id=row["llm"],
            memory_verify=row["memory_verify"],
            quality_assessment=row["quality_assessment"],
            is_default=bool(row["is_default"]),
        )

    async def scene(self, config_id: UUID | str, workspace_id: UUID) -> SceneConfigData:
        row = await require_owned_config(
            self.repository, config_id, workspace_id, columns=SCENE_COLUMNS
        )
        return self._validated(SceneConfigData, row)

    async def scene_community(
        self, config_id: UUID | str, workspace_id: UUID
    ) -> SceneCommunityConfigData:
        row = await require_owned_config(
            self.repository, config_id, workspace_id, columns=SCENE_COMMUNITY_COLUMNS
        )
        return self._validated(SceneCommunityConfigData, row)

    async def prediction(self, config_id: UUID | str, workspace_id: UUID) -> PredictionConfigData:
        row = await require_owned_config(
            self.repository, config_id, workspace_id, columns=PREDICTION_COLUMNS
        )
        return PredictionConfigData(
            config_id=row["config_id"],
            is_default=bool(row["is_default"]),
            **{field: row[field] for field in _PREDICTION_FIELDS},
        )

    async def preference(self, config_id: UUID | str, workspace_id: UUID) -> PreferenceConfigData:
        row = await require_owned_config(
            self.repository, config_id, workspace_id, columns=PREFERENCE_COLUMNS
        )
        custom = list(row["preference_custom_keywords"] or [])
        return PreferenceConfigData(
            config_id=row["config_id"],
            preference_engine_enabled=bool(row["preference_engine_enabled"]),
            default_keywords=list(DEFAULT_PREFERENCE_KEYWORDS),
            custom_keywords=custom,
            effective_keywords=effective_keywords(custom),
        )

    async def read_all(self, workspace_id: UUID) -> list[dict[str, Any]]:
        # The list and the workspace row are independent, so they run together.
        # New rows no longer store model ids, so the list reads them from the
        # workspace — the only place runtime resolution looks. The workspace row
        # already carries ``memory_config``, which is the active pointer; the
        # separate pointer query only filtered on ``is_active``, reproduced here.
        rows, workspace = await asyncio.gather(
            self.repository.list_by_workspace(workspace_id),
            self.workspaces.workspace_row(workspace_id),
        )
        if not rows:
            return []
        active_id = workspace["memory_config"] if workspace and workspace["is_active"] else None

        data: list[dict[str, Any]] = []
        for row, scene_name in rows:
            # Returned as data only: the legacy id is no longer accepted as input.
            config_id_old = row["config_id_old"]  # TODO:待移除
            data.append(
                {
                    "config_id": str(row["config_id"]),
                    "config_name": row["config_name"],
                    "config_desc": row["config_desc"],
                    "workspace_id": (str(row["workspace_id"]) if row["workspace_id"] else None),
                    "end_user_id": row["end_user_id"],
                    "config_id_old": config_id_old,  # TODO:待移除
                    "apply_id": row["apply_id"],
                    "scene_id": str(row["scene_id"]) if row["scene_id"] else None,
                    "scene_name": scene_name,
                    "is_system_default": row["is_default"],
                    "llm_id": workspace.get("llm") if workspace else None,
                    "embedding_id": workspace.get("embedding") if workspace else None,
                    "rerank_id": workspace.get("rerank") if workspace else None,
                    "enable_llm_dedup_blockwise": row["enable_llm_dedup_blockwise"],
                    "enable_llm_disambiguation": row["enable_llm_disambiguation"],
                    "deep_retrieval": row["deep_retrieval"],
                    "t_type_strict": row["t_type_strict"],
                    "t_name_strict": row["t_name_strict"],
                    "t_overall": row["t_overall"],
                    "state": row["state"],
                    "chunker_strategy": row["chunker_strategy"],
                    "pruning_enabled": row["pruning_enabled"],
                    "pruning_scene": row["pruning_scene"],
                    "pruning_threshold": row["pruning_threshold"],
                    "enable_self_reflexion": row["enable_self_reflexion"],
                    "iteration_period": row["iteration_period"],
                    "reflexion_range": row["reflexion_range"],
                    "baseline": row["baseline"],
                    "statement_granularity": row["statement_granularity"],
                    "include_dialogue_context": row["include_dialogue_context"],
                    "max_context": row["max_context"],
                    "lambda_time": row["lambda_time"],
                    "lambda_mem": row["lambda_mem"],
                    "offset": row["offset"],
                    "created_at": _to_timestamp_ms(row["created_at"]),
                    "updated_at": _to_timestamp_ms(row["updated_at"]),
                    "is_active": str(row["config_id"]) == str(active_id),
                }
            )
        return data

    async def active_config_id(self, workspace_id: UUID) -> UUID | None:
        """The workspace's active configuration pointer, or ``None``."""

        return await self.repository.get_workspace_active_config_id(workspace_id)

    async def owned_row(self, config_id: UUID | str, workspace_id: UUID) -> dict[str, Any]:
        """Load a configuration owned by ``workspace_id``.

        Callers that turn every lookup failure into one legacy response catch the
        typed ownership failures themselves.
        """

        return await require_owned_config(self.repository, config_id, workspace_id)

    @staticmethod
    def _validated(model: Any, row: dict[str, Any]) -> Any:
        try:
            return model.model_validate(row)
        except ValueError as exc:
            # Legacy: the response model raised inside the handler, i.e. a 500.
            raise ConfigValidationError(str(exc)) from exc
