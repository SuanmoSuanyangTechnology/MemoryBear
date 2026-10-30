"""Anti-corruption layer: host rows -> domain objects.

The host tables are owned by the API's migrations. Nothing from SQLAlchemy or
from the raw row shape is allowed past these functions.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from src.domain.end_user.model import (
    EndUser,
    MergeCandidate,
    MergeLink,
    ResolutionCandidates,
    UserStatus,
)
from src.domain.model_config.model import ModelConfigRow
from src.domain.profile.model import SourceVersion
from src.domain.profile.read_models import InsightSource, RefreshCandidate
from src.domain.shared.ids import EndUserId, ModelConfigId, TenantId, WorkspaceId
from src.domain.workspace.model import (
    ModelSlots,
    StoragePolicy,
    Workspace,
    WorkspaceStatus,
)


def _as_uuid(value: object) -> UUID:
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


def _optional_model_id(value: object) -> ModelConfigId | None:
    return None if value is None else ModelConfigId(_as_uuid(value))


def to_workspace(row: Mapping[str, Any]) -> Workspace:
    """Map one ``workspaces`` row (plus its model columns) to the aggregate."""

    raw_storage = row["storage_type"]
    storage_type_raw = None if raw_storage is None else str(raw_storage)
    return Workspace(
        id=WorkspaceId(_as_uuid(row["id"])),
        tenant_id=(
            None if row["tenant_id"] is None else TenantId(_as_uuid(row["tenant_id"]))
        ),
        name=str(row["name"] or ""),
        status=WorkspaceStatus.ACTIVE if row["is_active"] else WorkspaceStatus.INACTIVE,
        storage=StoragePolicy.parse(storage_type_raw),
        is_default_config=bool(row["is_default_config"]),
        memory_config=_optional_model_id(row["memory_config"]),
        models=ModelSlots(
            llm=_optional_model_id(row["llm"]),
            embedding=_optional_model_id(row["embedding"]),
            rerank=_optional_model_id(row["rerank"]),
            vision=_optional_model_id(row["vision"]),
            audio=_optional_model_id(row["audio"]),
            video=_optional_model_id(row["video"]),
        ),
        storage_type_raw=storage_type_raw,
    )


def to_end_user(row: Mapping[str, Any]) -> EndUser:
    """Map one ``end_users`` row to the directory entity."""

    return EndUser(
        id=EndUserId(_as_uuid(row["id"])),
        workspace_id=WorkspaceId(_as_uuid(row["workspace_id"])),
        display_name=row["other_name"],
        status=UserStatus.ACTIVE if row["is_active"] else UserStatus.INACTIVE,
    )


def to_resolution_candidates(
    origin_id: EndUserId,
    workspace_id: WorkspaceId,
    direct_row: Mapping[str, Any] | None,
    merge_rows: list[Mapping[str, Any]],
) -> ResolutionCandidates:
    """Build the direct hit plus the merge targets, newest merge first."""

    return ResolutionCandidates(
        workspace_id=workspace_id,
        direct=None if direct_row is None else to_end_user(direct_row),
        merges=tuple(
            MergeCandidate(
                link=MergeLink(
                    origin_id=origin_id,
                    target_id=EndUserId(_as_uuid(row["id"])),
                    workspace_id=WorkspaceId(_as_uuid(row["workspace_id"])),
                    sequence=int(row["sequence"]),
                ),
                target=to_end_user(row),
            )
            for row in merge_rows
        ),
    )


def to_insight_source(row: Mapping[str, Any]) -> InsightSource:
    """Map the joined ``end_users`` + ``end_user_info`` row to the read model."""

    metadata = row["meta_data"]
    return InsightSource(
        end_user_id=EndUserId(_as_uuid(row["end_user_id"])),
        workspace_id=WorkspaceId(_as_uuid(row["workspace_id"])),
        metadata=metadata if isinstance(metadata, Mapping) else {},
        version=SourceVersion(
            write_time=row["write_time"],
            metadata_updated_at=row["metadata_updated_at"],
            metadata_row_exists=row["metadata_id"] is not None,
        ),
    )


def to_refresh_candidate(row: Mapping[str, Any]) -> RefreshCandidate:
    """Map one scan row to the freshness policy's input."""

    return RefreshCandidate(
        end_user_id=EndUserId(_as_uuid(row["end_user_id"])),
        workspace_id=WorkspaceId(_as_uuid(row["workspace_id"])),
        insight_cached_at=row["memory_insight_updated_at"],
        summary_cached_at=row["user_summary_updated_at"],
        write_time=row["write_time"],
        metadata_updated_at=row["metadata_updated_at"],
    )


def to_model_config_row(row: Mapping[str, Any] | None) -> ModelConfigRow | None:
    """Map the one-row model-resolution result; ``None`` when nothing matched."""

    if row is None:
        return None
    tenant_id = row["tenant_id"]
    return ModelConfigRow(
        model_id=_optional_model_id(row["model_id"]),
        config_exists=bool(row["config_exists"]),
        model_row_id=row["model_row_id"],
        tenant_id=None if tenant_id is None else TenantId(_as_uuid(tenant_id)),
        is_active=bool(row["is_active"]),
        is_public=bool(row["is_public"]),
    )
