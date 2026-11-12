"""Read-only PostgreSQL access for memory-configuration rows.

Every statement is a ``select``; the boundary script enforces that. The
repositories select explicit columns rather than ORM entities, so each result
is a plain ``RowMapping`` keyed by column name and no session-bound object
survives.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from uuid import UUID

from sqlalchemy import desc, select

from src.infrastructure.postgres import PostgresManager
from src.models.references import MemoryConfigRow, Workspace


def _columns(*keys: str) -> tuple[Any, ...]:
    """A named projection; fails at import if the model loses a column."""

    return tuple(MemoryConfigRow.__table__.c[key] for key in keys)


#: Only the columns ``read_all`` returns; evaluated at import so a renamed or
#: removed column fails loudly instead of silently dropping a response field.
_SUMMARY_COLUMNS = _columns(
    "config_id",
    "config_id_old",
    "config_name",
    "config_desc",
    "workspace_id",
    "end_user_id",
    "apply_id",
    # read_all compatibility fields are projected from the existing config row;
    # no retired ontology table is joined.
    # 在v0.5.11实现这些契约变更和字段收缩。
    "scene_id",
    "is_default",
    "state",
    "chunker_strategy",
    "pruning_enabled",
    "pruning_scene",
    "pruning_threshold",
    "statement_granularity",
    "include_dialogue_context",
    "max_context",
    "enable_llm_dedup_blockwise",
    "enable_llm_disambiguation",
    "deep_retrieval",
    "t_type_strict",
    "t_name_strict",
    "t_overall",
    "enable_self_reflexion",
    "iteration_period",
    "reflexion_range",
    "baseline",
    "lambda_time",
    "lambda_mem",
    "offset",
    "created_at",
    "updated_at",
)


PERSONALITY_COLUMNS = _columns(
    "config_id",
    "is_default",
    "personality_anchor_enabled",
    "personality_anchor_auto_solidify",
    "personality_anchor_read_metadata",
    "personality_anchor_read_preferences",
    "personality_anchor_sync_cycle",
    "personality_anchor_base_principles",
)
EXTRACTED_COLUMNS = _columns(
    "is_default",
    "enable_llm_dedup_blockwise",
    "enable_llm_disambiguation",
    "deep_retrieval",
    "t_type_strict",
    "t_name_strict",
    "t_overall",
    "chunker_strategy",
    "statement_granularity",
    "include_dialogue_context",
    "max_context",
    "pruning_enabled",
    "pruning_scene",
    "pruning_threshold",
    "enable_self_reflexion",
    "iteration_period",
    "reflexion_range",
    "baseline",
)
FORGETTING_COLUMNS = _columns(
    "config_id",
    "is_default",
    "decay_constant",
    "lambda_time",
    "lambda_mem",
    "offset",
    "max_history_length",
    "forgetting_threshold",
    "min_days_since_access",
    "enable_llm_summary",
    "max_merge_batch_size",
    "forgetting_interval_hours",
)
EMOTION_COLUMNS = _columns(
    "config_id",
    "is_default",
    "emotion_enabled",
    "emotion_extract_keywords",
    "emotion_min_intensity",
    "emotion_enable_subject",
)
REFLECTION_COLUMNS = _columns(
    "config_id",
    "is_default",
    "enable_self_reflexion",
    "iteration_period",
    "reflexion_range",
    "baseline",
    "memory_verify",
    "quality_assessment",
)
SCENE_COLUMNS = _columns(
    "config_id",
    "scene_threshold",
    "scene_history_window_size",
    "scene_min_turns",
    "scene_max_turns",
    "scene_idle_timeout_seconds",
    "scene_min_chars_to_summary",
    "time_decay_penalty",
)
SCENE_COMMUNITY_COLUMNS = _columns(
    "config_id",
    "batch_trigger_count",
    "candidate_community_limit",
    "compare_all_same_category_communities",
    "rebuild_new_scene_community_count_enabled",
    "rebuild_new_scene_community_count",
    "rebuild_interval_enabled",
    "rebuild_interval_days",
)
PREDICTION_COLUMNS = _columns(
    "config_id",
    "is_default",
    "prediction_candidate_limit",
    "prediction_participant_limit",
    "prediction_max_steps",
    "prediction_recall_limit",
    "prediction_min_valid_memory_count",
    "prediction_embedding_min_similarity",
)
PREFERENCE_COLUMNS = _columns(
    "config_id",
    "preference_engine_enabled",
    "preference_custom_keywords",
)
IS_DEFAULT_COLUMN = _columns("is_default")

#: Workspace columns reported next to a configuration; the extraction payload
#: needs all six model slots, emotion and reflection only the LLM.
WORKSPACE_MODEL_COLUMNS = (
    Workspace.llm,
    Workspace.embedding,
    Workspace.rerank,
    Workspace.vision,
    Workspace.audio,
    Workspace.video,
)
WORKSPACE_LLM_COLUMN = (Workspace.llm,)

#: Columns the active-config validation reads: config identity plus the
#: workspace's tenant and six model slots.
VALIDATION_COLUMNS = _columns("config_id", "config_name", "workspace_id")
WORKSPACE_VALIDATION_COLUMNS = (
    Workspace.tenant_id,
    Workspace.llm,
    Workspace.embedding,
    Workspace.rerank,
    Workspace.vision,
    Workspace.audio,
    Workspace.video,
)


class MemoryConfigReadRepository:
    def __init__(self, postgres: PostgresManager) -> None:
        self.postgres = postgres

    async def get_with_workspace(self, config_id: UUID) -> dict[str, Any] | None:
        """One configuration joined with its workspace's tenant and model slots.

        The active-config validation needs exactly these ten columns; reading the
        full row and then a separate workspace row cost two round trips and 82
        columns that were thrown away. The join is outer because the legacy code
        treats a NULL or dangling ``workspace_id`` as "no models configured"
        (warnings) rather than "configuration not found".
        """

        async with self.postgres.session() as session:
            row = (
                (
                    await session.execute(
                        select(*VALIDATION_COLUMNS, *WORKSPACE_VALIDATION_COLUMNS)
                        .outerjoin(Workspace, Workspace.id == MemoryConfigRow.workspace_id)
                        .where(MemoryConfigRow.config_id == config_id)
                        .limit(1)
                    )
                )
                .mappings()
                .one_or_none()
            )
            return dict(row) if row is not None else None

    async def exists(self, config_id: UUID) -> bool:
        """Whether a configuration exists, regardless of its workspace."""

        async with self.postgres.session() as session:
            found = await session.scalar(
                select(MemoryConfigRow.config_id)
                .where(MemoryConfigRow.config_id == config_id)
                .limit(1)
            )
            return found is not None

    async def exists_owned(self, config_id: UUID, workspace_id: UUID) -> bool:
        """Whether a configuration exists inside ``workspace_id``."""

        async with self.postgres.session() as session:
            found = await session.scalar(
                select(MemoryConfigRow.config_id)
                .where(
                    MemoryConfigRow.config_id == config_id,
                    MemoryConfigRow.workspace_id == workspace_id,
                )
                .limit(1)
            )
            return found is not None

    async def get_owned(
        self,
        config_id: UUID,
        workspace_id: UUID,
        *,
        columns: Iterable[Any] | None = None,
        workspace_columns: Iterable[Any] | None = None,
    ) -> dict[str, Any] | None:
        """Load one owned row, optionally joined with its workspace row.

        Callers pass only the columns they will read; ``None`` selects the whole
        table for the few paths that need it. ``workspace_columns`` adds those
        workspace columns to the same statement — one round trip instead of two
        — and the joined columns keep their own names.
        """

        selected = tuple(MemoryConfigRow.__table__.c) if columns is None else tuple(columns)
        statement = select(*selected)
        if workspace_columns is not None:
            statement = statement.join(
                Workspace, Workspace.id == MemoryConfigRow.workspace_id
            ).add_columns(*workspace_columns)
        async with self.postgres.session() as session:
            row = (
                (
                    await session.execute(
                        statement.where(
                            MemoryConfigRow.config_id == config_id,
                            MemoryConfigRow.workspace_id == workspace_id,
                        ).limit(1)
                    )
                )
                .mappings()
                .one_or_none()
            )
            return dict(row) if row is not None else None

    async def list_by_workspace(self, workspace_id: UUID) -> list[dict[str, Any]]:
        """List a workspace's configurations."""

        async with self.postgres.session() as session:
            rows = (
                (
                    await session.execute(
                        select(*_SUMMARY_COLUMNS)
                        .where(MemoryConfigRow.workspace_id == workspace_id)
                        .order_by(desc(MemoryConfigRow.updated_at))
                    )
                )
                .mappings()
                .all()
            )
        return [dict(row) for row in rows]

    async def get_workspace_active_config_id(self, workspace_id: UUID) -> UUID | None:
        """The active workspace row's ``memory_config`` pointer."""

        async with self.postgres.session() as session:
            return await session.scalar(
                select(Workspace.memory_config).where(
                    Workspace.id == workspace_id,
                    Workspace.is_active.is_(True),
                )
            )
