"""Write use cases for the memory-configuration module (P2)."""

from __future__ import annotations

import logging
import unicodedata
import uuid
from typing import TYPE_CHECKING, Any
from uuid import UUID

from memory_contracts.preferences import (
    DEFAULT_PREFERENCE_KEYWORDS,
    normalize_keyword,
)
from sqlalchemy.exc import IntegrityError

from src.common.redis_cache import invalidate_cache
from src.infrastructure.postgres import PostgresManager
from src.repositories.memory_config import (
    IS_DEFAULT_COLUMN,
    PREFERENCE_COLUMNS,
    MemoryConfigReadRepository,
)
from src.repositories.memory_config_mutation import MemoryConfigMutationRepository
from src.repositories.workspace import WorkspaceRepository
from src.schemas.memory_config import (
    ActiveConfigResult,
    ConfigCreateResult,
    ConfigUpdateResult,
    EmotionConfigData,
    ForgettingConfigData,
    PersonalityAnchorData,
    PredictionConfigData,
    PreferenceConfigData,
    SceneCommunityConfigData,
    SceneConfigData,
)
from src.schemas.memory_config_requests import (
    ConfigCreateBody,
    ConfigUpdateBody,
    EmotionUpdateBody,
    ExtractedUpdateBody,
    ForgettingUpdateBody,
    PredictionUpdateBody,
    PreferenceUpdateBody,
    ReflectionUpdateBody,
    SceneCommunityUpdateBody,
    SceneUpdateBody,
)
from src.services.memory_config.errors import (
    ConfigNotFoundError,
    ConfigQueryError,
    DuplicateConfigNameError,
    NoFieldsToUpdateError,
    WorkspaceNotFoundError,
)
from src.services.memory_config.ownership import (
    require_owned_config,
    require_owned_config_exists,
    resolve_config_id,
)
from src.services.memory_config.query import MemoryConfigQueryService
from src.services.quota import admit_memory_engine

if TYPE_CHECKING:
    from src.services.memory_config_trial.validation import ValidConfigService

logger = logging.getLogger(__name__)

_PERSONALITY_FIELDS = (
    "personality_anchor_enabled",
    "personality_anchor_auto_solidify",
    "personality_anchor_read_metadata",
    "personality_anchor_read_preferences",
    "personality_anchor_sync_cycle",
    "personality_anchor_base_principles",
)
#: Model-id columns on ``memory_config`` are a legacy snapshot: runtime
#: resolution reads the workspace row (or its default preset), so new rows do
#: not store them at all.
_PREDICTION_FIELDS = (
    "prediction_candidate_limit",
    "prediction_participant_limit",
    "prediction_max_steps",
    "prediction_recall_limit",
    "prediction_min_valid_memory_count",
    "prediction_embedding_min_similarity",
)


def _normalize_list(values: list[str] | None, field_name: str) -> list[str]:
    normalized: list[str] = []
    for value in values or []:
        if not isinstance(value, str):
            raise ValueError(f"{field_name} must contain strings")
        keyword = normalize_keyword(value)
        if not keyword:
            raise ValueError(f"{field_name} contains an empty keyword")
        if len(keyword) > 40:
            raise ValueError(f"{field_name} keyword exceeds 40 Unicode characters")
        if any(unicodedata.category(char).startswith("C") for char in keyword):
            raise ValueError(f"{field_name} keyword contains control characters")
        normalized.append(keyword)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field_name} contains normalized duplicates")
    return normalized


class MemoryConfigMutationService:
    def __init__(
        self,
        runtime: Any,
        *,
        query: MemoryConfigQueryService,
        validation: ValidConfigService,
    ) -> None:
        postgres: PostgresManager = runtime.postgres
        self.postgres = postgres
        self.repository = MemoryConfigReadRepository(postgres)
        self.mutations = MemoryConfigMutationRepository(postgres)
        self.workspaces = WorkspaceRepository(postgres)
        # Shared read/probe collaborators are injected by the composition root
        # (the handler) so this service does not build them behind its caller's
        # back.
        self.query = query
        self.validation = validation
        self.quota = runtime.quota

    # ── Create ───────────────────────────────────────────────────────────

    async def create(
        self,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        body: ConfigCreateBody,
    ) -> ConfigCreateResult:
        workspace = await self.workspaces.workspace_row(workspace_id)
        if workspace is None:
            raise ConfigQueryError(f"工作空间不存在: workspace_id={workspace_id}")
        resolved_tenant = tenant_id or workspace["tenant_id"]

        # Legacy order: quota admission precedes duplicate detection and insert.
        async with self.postgres.session() as session:
            outcome = await admit_memory_engine(session, resolved_tenant, workspace_id, self.quota)
        outcome.require()

        if body.config_name and await self.mutations.find_by_workspace_and_name(
            workspace_id, body.config_name
        ):
            raise DuplicateConfigNameError(body.config_name)

        values = body.model_dump()
        values["workspace_id"] = workspace_id
        values["config_id"] = uuid.uuid4()

        try:
            config_id = await self.mutations.create(values)
        except IntegrityError as exc:
            detail = str(getattr(exc, "orig", exc))
            if "uq_workspace_config_name" in detail:
                raise DuplicateConfigNameError(body.config_name) from exc
            raise

        return ConfigCreateResult(affected=1, config_id=str(config_id))

    # ── Updates ──────────────────────────────────────────────────────────

    async def update_personality(
        self, config_id: UUID, workspace_id: UUID, values: dict[str, Any]
    ) -> PersonalityAnchorData:
        await require_owned_config_exists(self.repository, config_id, workspace_id)
        payload = {field: values[field] for field in _PERSONALITY_FIELDS}
        if not await self.mutations.update_owned(config_id, workspace_id, payload):
            raise ConfigNotFoundError(str(config_id))
        try:
            await invalidate_cache(prefix=f"memory_config:{config_id}")
        except Exception:
            logger.exception(
                "personality-anchor saved but cache invalidation failed: %s", config_id
            )
        return await self.query.personality_anchor(config_id, workspace_id)

    async def update_basic(self, body: ConfigUpdateBody, workspace_id: UUID) -> ConfigUpdateResult:
        config_id = await resolve_config_id(self.repository, body.config_id)
        await require_owned_config_exists(self.repository, config_id, workspace_id)
        if body.config_name is None and body.config_desc is None and body.scene_id is None:
            raise NoFieldsToUpdateError("config_name, config_desc, scene_id")
        values: dict[str, Any] = {}
        if body.config_name is not None:
            values["config_name"] = body.config_name
        if body.config_desc is not None:
            values["config_desc"] = body.config_desc
        if body.scene_id is not None:
            values["scene_id"] = body.scene_id
        if not await self.mutations.update_owned(config_id, workspace_id, values):
            raise ConfigNotFoundError(str(config_id))
        return ConfigUpdateResult(affected=1)

    async def update_extracted(
        self, body: ExtractedUpdateBody, workspace_id: UUID
    ) -> ConfigUpdateResult:
        config_id = await resolve_config_id(self.repository, body.config_id)
        await require_owned_config_exists(self.repository, config_id, workspace_id)
        values = body.model_dump(exclude_unset=True)
        values.pop("config_id", None)
        if not await self.mutations.update_owned(config_id, workspace_id, values):
            raise ConfigQueryError("配置不存在")
        return ConfigUpdateResult(affected=1)

    async def update_forgetting(
        self, body: ForgettingUpdateBody, workspace_id: UUID
    ) -> ForgettingConfigData:
        config_id = await resolve_config_id(self.repository, body.config_id)
        await require_owned_config_exists(self.repository, config_id, workspace_id)
        values = body.model_dump(exclude_none=True)
        values.pop("config_id", None)
        if not await self.mutations.update_owned(config_id, workspace_id, values):
            raise ConfigNotFoundError(str(config_id))
        return await self.query.forgetting(config_id, workspace_id)

    async def update_emotion(
        self, body: EmotionUpdateBody, workspace_id: UUID
    ) -> EmotionConfigData:
        config_id = await resolve_config_id(self.repository, body.config_id)
        await require_owned_config_exists(self.repository, config_id, workspace_id)
        values = body.model_dump(exclude={"config_id"})
        if not await self.mutations.update_owned(config_id, workspace_id, values):
            raise ConfigNotFoundError(str(config_id))
        try:
            await invalidate_cache(prefix=f"memory_config:{config_id}")
        except Exception:
            logger.exception("emotion config saved but cache invalidation failed: %s", config_id)
        return await self.query.emotion(config_id, workspace_id)

    async def update_reflection(
        self, body: ReflectionUpdateBody, workspace_id: UUID
    ) -> dict[str, Any]:
        if body.config_id is None:
            raise ConfigQueryError("missing config_id")
        config_id = await resolve_config_id(self.repository, body.config_id)
        await require_owned_config_exists(self.repository, config_id, workspace_id)
        values = {
            "enable_self_reflexion": body.reflection_enabled,
            "iteration_period": body.reflection_period_in_hours,
            "reflexion_range": body.reflexion_range,
            "baseline": body.baseline,
            "memory_verify": body.memory_verify,
            "quality_assessment": body.quality_assessment,
        }
        if not await self.mutations.update_owned(config_id, workspace_id, values):
            raise ConfigNotFoundError(str(config_id))
        return {
            "config_id": config_id,
            "enable_self_reflexion": body.reflection_enabled,
            "iteration_period": body.reflection_period_in_hours,
            "reflexion_range": body.reflexion_range,
            "baseline": body.baseline,
            "memory_verify": body.memory_verify,
            "quality_assessment": body.quality_assessment,
        }

    async def update_scene(self, body: SceneUpdateBody, workspace_id: UUID) -> SceneConfigData:
        config_id = body.config_id
        await require_owned_config_exists(self.repository, config_id, workspace_id)
        values = body.model_dump(exclude={"config_id"})
        if not await self.mutations.update_owned(config_id, workspace_id, values):
            raise ConfigNotFoundError(str(config_id))
        data = await self.query.scene(config_id, workspace_id)
        # Legacy behaviour: invalidation failures fail the request.
        await invalidate_cache(prefix=f"memory_config:{config_id}")
        return data

    async def update_scene_community(
        self, body: SceneCommunityUpdateBody, workspace_id: UUID
    ) -> SceneCommunityConfigData:
        config_id = body.config_id
        await require_owned_config_exists(self.repository, config_id, workspace_id)
        values = body.model_dump(exclude={"config_id"})
        if not await self.mutations.update_owned(config_id, workspace_id, values):
            raise ConfigNotFoundError(str(config_id))
        data = await self.query.scene_community(config_id, workspace_id)
        await invalidate_cache(prefix=f"memory_config:{config_id}")
        return data

    async def update_preference(
        self, body: PreferenceUpdateBody, workspace_id: UUID, operator: str
    ) -> PreferenceConfigData:
        normalized_custom: list[str] | None = None
        if body.custom_keywords is not None:
            normalized_custom = _normalize_list(body.custom_keywords, "custom_keywords")
            default_set = {normalize_keyword(item) for item in DEFAULT_PREFERENCE_KEYWORDS}
            normalized_custom = [
                keyword for keyword in normalized_custom if keyword not in default_set
            ]
            if len(normalized_custom) > 100:
                raise ValueError("a config can contain at most 100 custom keywords")

        values: dict[str, object] = {}
        if body.preference_engine_enabled is not None:
            values["preference_engine_enabled"] = body.preference_engine_enabled
        if normalized_custom is not None:
            values["preference_custom_keywords"] = normalized_custom

        row = await require_owned_config(
            self.repository, body.config_id, workspace_id, columns=PREFERENCE_COLUMNS
        )
        config_id = row["config_id"]
        updated = await self.mutations.update_owned(
            config_id, workspace_id, values, for_update=True
        )
        if not updated:
            raise ConfigNotFoundError(str(config_id))

        logger.info(
            "preference config updated operator=%s config_id=%s enabled=%s->%s custom_keywords=%s",
            operator,
            config_id,
            bool(row["preference_engine_enabled"]),
            bool(values.get("preference_engine_enabled", row["preference_engine_enabled"])),
            len(normalized_custom) if normalized_custom is not None else "unchanged",
        )
        # Legacy behaviour: invalidation failures fail the request.
        await invalidate_cache(prefix=f"memory_config:{config_id}")
        await invalidate_cache(prefix=f"preference_keywords:{config_id}")
        return await self.query.preference(config_id, workspace_id)

    async def update_prediction(
        self, body: PredictionUpdateBody, workspace_id: UUID
    ) -> PredictionConfigData:
        config_id = body.config_id
        await require_owned_config_exists(self.repository, config_id, workspace_id)
        values = {field: getattr(body, field) for field in _PREDICTION_FIELDS}
        if not await self.mutations.update_owned(config_id, workspace_id, values):
            raise ConfigNotFoundError(str(config_id))
        try:
            await invalidate_cache(prefix=f"memory_config:{config_id}")
        except Exception:
            logger.exception("prediction config saved but cache invalidation failed: %s", config_id)
        return await self.query.prediction(config_id, workspace_id)

    # ── Delete / list / active ───────────────────────────────────────────

    async def delete(self, raw_config_id: UUID | str, workspace_id: UUID) -> dict[str, Any]:
        config_id = await resolve_config_id(self.repository, raw_config_id)
        row = await require_owned_config(
            self.repository,
            config_id,
            workspace_id,
            columns=IS_DEFAULT_COLUMN,
        )
        if row["is_default"]:
            return {
                "status": "error",
                "message_key": "memory_config.delete.default_not_allowed",
                "is_default": True,
            }

        try:
            active_id = await self.repository.get_workspace_active_config_id(workspace_id)
        except Exception:
            active_id = None

        if active_id is not None and str(config_id) == str(active_id):
            return {
                "status": "warning",
                "message_key": "memory_config.delete.in_use",
                "force_required": True,
            }

        try:
            deleted = await self.mutations.delete_owned(config_id, workspace_id)
        except IntegrityError as exc:
            detail = str(getattr(exc, "orig", exc))
            if "ForeignKeyViolation" in detail or "foreign key constraint" in detail.lower():
                return {
                    "status": "error",
                    "message_key": "memory_config.delete.referenced_by_end_users",
                    "force_required": True,
                }
            raise
        if not deleted:
            raise ConfigNotFoundError(str(config_id))
        return {"status": "success", "message_key": "memory_config.success.delete"}

    async def active(self, workspace_id: UUID, config_id: UUID, locale: str) -> ActiveConfigResult:
        # Legacy order: the workspace is checked before the config is probed.
        workspace = await self.workspaces.workspace_row(workspace_id)
        if workspace is None or not workspace["is_active"]:
            raise WorkspaceNotFoundError(str(workspace_id))
        validation = await self.validation.valid_config(config_id, locale=locale)
        updated = await self.mutations.set_workspace_active(workspace_id, config_id)
        if not updated:
            raise WorkspaceNotFoundError(str(workspace_id))
        return ActiveConfigResult(
            config_id=config_id,
            warnings=validation.get("warnings", []),
            success=True,
        )
