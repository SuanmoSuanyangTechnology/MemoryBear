"""Read models the profile context needs from the graph and the host tables."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from src.domain.profile.model import SourceVersion
from src.domain.shared.ids import EndUserId, WorkspaceId


@dataclass(frozen=True, slots=True)
class InsightSource:
    """The profile metadata and the version the insight is generated from."""

    end_user_id: EndUserId
    workspace_id: WorkspaceId
    metadata: Mapping[str, object]
    version: SourceVersion


@dataclass(frozen=True, slots=True)
class StatementSample:
    statement: str
    created_at: datetime | None


@dataclass(frozen=True, slots=True)
class EntityTag:
    name: str
    frequency: int


@dataclass(frozen=True, slots=True)
class RefreshCandidate:
    """One user's cache timestamps and the sources they should track.

    ``insight_cached_at`` / ``summary_cached_at`` come from ``end_users``; the
    source timestamps come from ``end_users.write_time`` and the outer-joined
    ``end_user_info.updated_at``.
    """

    end_user_id: EndUserId
    workspace_id: WorkspaceId
    insight_cached_at: datetime | None
    summary_cached_at: datetime | None
    write_time: datetime | None
    metadata_updated_at: datetime | None
