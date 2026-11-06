"""Read cached profiles using the original API's filtering and error semantics."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from src.common.errors import BizCode, LocalizedProfileError, ProfileError
from src.repositories.implicit_memory import ImplicitMemoryRepository

if TYPE_CHECKING:
    from src.runtime import ProfileRuntime

logger = logging.getLogger(__name__)


def implicit_read_error(
    exc: Exception, operation_key: str, *, include_detail: bool
) -> LocalizedProfileError:
    """Preserve the API's manager/API-key distinction for cached-read failures."""
    error_text = str(exc).lower()
    if isinstance(exc, ValueError):
        if "user" in error_text and "not found" in error_text:
            code, message_key = BizCode.INVALID_USER_ID, "memory.implicit.error.invalid_user_id"
        elif "insufficient" in error_text or "no data" in error_text:
            code, message_key = BizCode.INSUFFICIENT_DATA, "memory.implicit.error.insufficient_data"
        else:
            code, message_key = (
                BizCode.INVALID_FILTER_PARAMS,
                "memory.implicit.error.invalid_parameters",
            )
    elif isinstance(exc, KeyError):
        code, message_key = BizCode.INSUFFICIENT_DATA, "memory.implicit.error.missing_data"
    elif isinstance(exc, (ConnectionError, TimeoutError)):
        code, message_key = BizCode.SERVICE_UNAVAILABLE, "memory.implicit.error.service_unavailable"
    elif "analysis" in error_text or "llm" in error_text:
        code, message_key = BizCode.ANALYSIS_FAILED, "memory.implicit.error.analysis_failed"
    elif "storage" in error_text or "database" in error_text:
        code, message_key = BizCode.PROFILE_STORAGE_ERROR, "memory.implicit.error.storage_failed"
    else:
        code, message_key = BizCode.INTERNAL_ERROR, operation_key
    logger.error("Implicit memory read failed: operation=%s", operation_key, exc_info=exc)
    return LocalizedProfileError(
        code,
        message_key,
        detail_key=message_key if include_detail else None,
        status_code=200,
    )


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

    async def _profile(self, *, end_user_id: str | UUID, workspace_id: UUID) -> Any:
        # The old manager's get_cached_profile logs storage failures and returns
        # None; it does not reinterpret them as a new business error.
        try:
            return await self.repository.get_profile(end_user_id, workspace_id)
        except Exception:
            logger.exception("从数据库获取用户画像失败: user=%s", end_user_id)
            return None

    async def check_data(self, *, end_user_id: str | UUID, workspace_id: UUID) -> bool:
        return await self._profile(end_user_id=end_user_id, workspace_id=workspace_id) is not None

    async def _required_profile(self, *, end_user_id: str | UUID, workspace_id: UUID) -> Any:
        profile = await self._profile(end_user_id=end_user_id, workspace_id=workspace_id)
        if profile is None:
            raise ProfileError(BizCode.NOT_FOUND, "", detail="", status_code=200)
        return profile

    async def preferences(
        self,
        *,
        end_user_id: str | UUID,
        workspace_id: UUID,
        confidence_threshold: float,
        tag_category: str | None,
        start_date: datetime | None,
        end_date: datetime | None,
    ) -> list[Any]:
        profile = await self._required_profile(end_user_id=end_user_id, workspace_id=workspace_id)
        result = []
        for preference in profile.get("preferences", []):
            if (
                confidence_threshold is not None
                and preference.get("confidence_score", 0) < confidence_threshold
            ):
                continue
            if tag_category and preference.get("category") != tag_category:
                continue
            if start_date or end_date:
                created_at_ts = preference.get("created_at")
                if created_at_ts:
                    # Intentionally retain the original process-local conversion.
                    # Adding ISO parsing or UTC normalization changes old results.
                    created = datetime.fromtimestamp(created_at_ts / 1000)
                    if start_date and created < start_date:
                        continue
                    if end_date and created > end_date:
                        continue
            result.append(preference)
        return result

    async def portrait(self, *, end_user_id: str | UUID, workspace_id: UUID) -> Any:
        profile = await self._required_profile(end_user_id=end_user_id, workspace_id=workspace_id)
        return profile.get("portrait", {})

    async def interest_areas(self, *, end_user_id: str | UUID, workspace_id: UUID) -> Any:
        profile = await self._required_profile(end_user_id=end_user_id, workspace_id=workspace_id)
        return profile.get("interest_areas", {})

    async def habits(
        self,
        *,
        end_user_id: str | UUID,
        workspace_id: UUID,
        confidence_level: str | None,
        frequency_pattern: str | None,
        time_period: str | None,
    ) -> list[Any]:
        profile = await self._required_profile(end_user_id=end_user_id, workspace_id=workspace_id)
        result = []
        for habit in profile.get("habits", []):
            if confidence_level:
                minimum = {"high": 85, "medium": 50, "low": 20}.get(confidence_level.lower())
                if habit.get("confidence_level", 0) < minimum:
                    continue
            if frequency_pattern and habit.get("frequency_pattern") != frequency_pattern:
                continue
            if time_period:
                is_current = habit.get("is_current", True)
                if time_period.lower() == "current" and not is_current:
                    continue
                if time_period.lower() == "past" and is_current:
                    continue
            result.append(habit)
        return result

    async def aggregate(
        self, *, end_user_id: str | UUID, workspace_id: UUID
    ) -> dict[str, Any] | None:
        # The API-key endpoint did not swallow repository failures.
        profile = await self.repository.get_profile(end_user_id, workspace_id)
        if profile is None:
            return None
        preferences = [
            preference
            for preference in profile.get("preferences", [])
            if preference.get("confidence_score", 0) >= 0.5
        ]
        return {
            "end_user_id": str(end_user_id),
            "habits": profile.get("habits", []),
            "preferences": preferences,
            "portrait": profile.get("portrait", {}),
            "interest_areas": profile.get("interest_areas", {}),
        }
