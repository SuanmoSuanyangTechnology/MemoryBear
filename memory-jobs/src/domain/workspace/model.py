"""Workspace aggregate: the facts and rules that gate cache generation.

The aggregate is built from the host's ``workspaces`` row by the persistence
mapper. It owns two invariants that used to live in a free function operating on
a ``dict``: only an active workspace may generate, and only a Neo4j-backed one
can be read by the graph pipelines.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from src.domain.shared.errors import ModelConfigurationError, WorkspaceNotEligible
from src.domain.shared.ids import ModelConfigId, TenantId, WorkspaceId


class WorkspaceStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class StoragePolicy(StrEnum):
    NEO4J = "neo4j"

    @classmethod
    def parse(cls, raw: str | None) -> StoragePolicy | None:
        """Interpret the host column; a missing value predates the column and means Neo4j."""

        value = StoragePolicy.NEO4J.value if raw is None else str(raw).strip().lower()
        return cls.NEO4J if value == cls.NEO4J.value else None


@dataclass(frozen=True, slots=True)
class ModelSlots:
    """The model columns a workspace carries, one per slot."""

    llm: ModelConfigId | None = None
    embedding: ModelConfigId | None = None
    rerank: ModelConfigId | None = None
    vision: ModelConfigId | None = None
    audio: ModelConfigId | None = None
    video: ModelConfigId | None = None


@dataclass(frozen=True, slots=True)
class Workspace:
    """Aggregate root for workspace eligibility and model configuration."""

    id: WorkspaceId
    tenant_id: TenantId | None
    name: str
    status: WorkspaceStatus
    storage: StoragePolicy | None
    is_default_config: bool
    memory_config: ModelConfigId | None
    models: ModelSlots
    #: The host's declared value, kept for diagnostics when it is unsupported.
    storage_type_raw: str | None = None

    def ensure_generation_allowed(self) -> None:
        """Reject a workspace the graph pipelines cannot serve.

        The reason strings are part of the producer contract: the same values
        are returned by the pre-publish check in memory-profile.
        """

        if self.status is not WorkspaceStatus.ACTIVE:
            raise WorkspaceNotEligible(self.id, "workspace_inactive")
        if self.storage is not StoragePolicy.NEO4J:
            raise WorkspaceNotEligible(self.id, "unsupported_storage_type")

    def ensure_model_resolution_allowed(self) -> None:
        if self.memory_config is None:
            raise ModelConfigurationError(f"空间{self.id}无启用的记忆配置")
