"""Merge routing, the one rule the directory asked the domain to own."""

from __future__ import annotations

from src.domain.end_user.model import (
    ResolutionCandidates,
    UserResolution,
    UserStatus,
)


class UserResolutionService:
    """Resolve an id to the user whose data should be read or written.

    A direct, active user wins. Otherwise the newest merge whose target is
    active and still in the same workspace wins. Candidates arrive newest-first;
    the ordering is a performance hint, the rule is decided here.
    """

    def resolve(self, candidates: ResolutionCandidates) -> UserResolution | None:
        direct = candidates.direct
        if direct is not None and direct.status is UserStatus.ACTIVE:
            return UserResolution(direct, merged=False)

        for candidate in candidates.merges:
            target = candidate.target
            if target.workspace_id != candidates.workspace_id:
                continue
            if target.status is not UserStatus.ACTIVE:
                continue
            return UserResolution(target, merged=True)
        return None
