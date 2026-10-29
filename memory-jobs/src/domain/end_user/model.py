"""End-user identity and merge routing.

A user id may point at an account that was merged into another one inside the
same workspace. The directory exposes the candidates and the domain service
decides: a direct, active hit wins; otherwise the newest valid merge target does.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from src.domain.shared.ids import EndUserId, WorkspaceId


class UserStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"


@dataclass(frozen=True, slots=True)
class EndUser:
    id: EndUserId
    workspace_id: WorkspaceId
    display_name: str | None
    status: UserStatus


@dataclass(frozen=True, slots=True)
class MergeLink:
    """One ``end_user_merge`` row; ``sequence`` is the merge row id."""

    origin_id: EndUserId
    target_id: EndUserId
    workspace_id: WorkspaceId
    sequence: int


@dataclass(frozen=True, slots=True)
class MergeCandidate:
    link: MergeLink
    target: EndUser


@dataclass(frozen=True, slots=True)
class ResolutionCandidates:
    """Everything needed to resolve one id, ordered newest merge first."""

    workspace_id: WorkspaceId
    direct: EndUser | None
    merges: tuple[MergeCandidate, ...]


@dataclass(frozen=True, slots=True)
class UserResolution:
    user: EndUser
    merged: bool
