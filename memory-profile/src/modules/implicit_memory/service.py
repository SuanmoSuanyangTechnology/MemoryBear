"""Read-only implicit memory profile service."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from src.common.errors import BizCode, ProfileError
from src.common.normalization import as_utc_naive
from src.common.user_scope import require_user_in_workspace
from src.modules.implicit_memory.repository.implicit_memory import (
    ImplicitMemoryRepository,
)
from src.runtime import ProfileRuntime


def _profile_timestamp(value: Any) -> datetime | None:
    if isinstance(value, (int, float)):
        # Preserve the legacy API behavior: epoch values were converted in the
        # process-local timezone before comparing them with request datetimes.
        return datetime.fromtimestamp(value / 1000)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return as_utc_naive(parsed)
    return None


class ImplicitMemoryService:
    def __init__(
        self,
        runtime: ProfileRuntime | None = None,
        *,
        repository: ImplicitMemoryRepository | None = None,
    ) -> None:
        if repository is not None:
            self.repository = repository
        elif runtime is not None:
            self.repository = ImplicitMemoryRepository(runtime.postgres)
        else:
            raise ValueError("Either runtime or repository must be provided")

    async def _profile(
        self, *, end_user_id: UUID, workspace_id: UUID
    ) -> dict[str, Any] | None:
        await require_user_in_workspace(
            self.repository,
            end_user_id=end_user_id,
            workspace_id=workspace_id,
        )
        return await self.repository.get_profile(end_user_id)

    async def check_data(self, *, end_user_id: UUID, workspace_id: UUID) -> bool:
        return await self._profile(
            end_user_id=end_user_id,
            workspace_id=workspace_id,
        ) is not None

    async def _required_profile(
        self, *, end_user_id: UUID, workspace_id: UUID
    ) -> dict[str, Any]:
        profile = await self._profile(
            end_user_id=end_user_id,
            workspace_id=workspace_id,
        )
        if profile is None:
            raise ProfileError(
                BizCode.NOT_FOUND,
                "",
                detail="",
                status_code=404,
            )
        return profile

    async def preferences(
        self,
        *,
        end_user_id: UUID,
        workspace_id: UUID,
        confidence_threshold: float,
        tag_category: str | None,
        start_date: datetime | None,
        end_date: datetime | None,
    ) -> list[dict[str, Any]]:
        profile = await self._required_profile(
            end_user_id=end_user_id,
            workspace_id=workspace_id,
        )
        preferences = profile.get("preferences", [])
        if not isinstance(preferences, list):
            return []
        start = as_utc_naive(start_date) if start_date else None
        end = as_utc_naive(end_date) if end_date else None
        result = []
        for preference in preferences:
            if not isinstance(preference, dict):
                continue
            if preference.get("confidence_score", 0) < confidence_threshold:
                continue
            if tag_category and preference.get("category") != tag_category:
                continue
            if start or end:
                created = _profile_timestamp(preference.get("created_at"))
                if created is not None:
                    if start and created < start:
                        continue
                    if end and created > end:
                        continue
            result.append(preference)
        return result

    async def portrait(self, *, end_user_id: UUID, workspace_id: UUID) -> dict[str, Any]:
        profile = await self._required_profile(
            end_user_id=end_user_id,
            workspace_id=workspace_id,
        )
        portrait = profile.get("portrait", {})
        return portrait if isinstance(portrait, dict) else {}

    async def interest_areas(
        self, *, end_user_id: UUID, workspace_id: UUID
    ) -> dict[str, Any]:
        profile = await self._required_profile(
            end_user_id=end_user_id,
            workspace_id=workspace_id,
        )
        interest_areas = profile.get("interest_areas", {})
        return interest_areas if isinstance(interest_areas, dict) else {}

    async def habits(
        self,
        *,
        end_user_id: UUID,
        workspace_id: UUID,
        confidence_level: str | None,
        frequency_pattern: str | None,
        time_period: str | None,
    ) -> list[dict[str, Any]]:
        profile = await self._required_profile(
            end_user_id=end_user_id,
            workspace_id=workspace_id,
        )
        habits = profile.get("habits", [])
        if not isinstance(habits, list):
            return []
        confidence_mapping = {"high": 85, "medium": 50, "low": 20}
        result = []
        for habit in habits:
            if not isinstance(habit, dict):
                continue
            if confidence_level:
                minimum = confidence_mapping[confidence_level]
                if habit.get("confidence_level", 0) < minimum:
                    continue
            if frequency_pattern and habit.get("frequency_pattern") != frequency_pattern:
                continue
            if time_period:
                is_current = habit.get("is_current", True)
                if time_period == "current" and not is_current:
                    continue
                if time_period == "past" and is_current:
                    continue
            result.append(habit)
        return result

    async def aggregate(
        self, *, end_user_id: UUID, workspace_id: UUID
    ) -> dict[str, Any] | None:
        profile = await self._profile(
            end_user_id=end_user_id,
            workspace_id=workspace_id,
        )
        if profile is None:
            return None
        habits = profile.get("habits", [])
        preferences = [
            preference
            for preference in profile.get("preferences", [])
            if isinstance(preference, dict)
            and preference.get("confidence_score", 0) >= 0.5
        ]
        portrait = profile.get("portrait", {})
        interest_areas = profile.get("interest_areas", {})
        return {
            "end_user_id": str(end_user_id),
            "habits": habits if isinstance(habits, list) else [],
            "preferences": preferences,
            "portrait": portrait if isinstance(portrait, dict) else {},
            "interest_areas": (
                interest_areas if isinstance(interest_areas, dict) else {}
            ),
        }
