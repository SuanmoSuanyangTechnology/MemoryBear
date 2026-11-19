"""Read cached profiles using the original API's filtering and error semantics."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any, List, Optional
from uuid import UUID

from src.common.errors import BizCode, LocalizedProfileError, ProfileError
from src.schemas.memory_config import MemoryConfig
from src.common.datetime_utils import to_timestamp_ms, utcnow_naive
from src.repositories.implicit_memory import ImplicitMemoryRepository

if TYPE_CHECKING:
    from src.runtime import ProfileRuntime

from src.schemas.implicit_memory import (
    BehaviorHabit, DateRange, DimensionPortrait, FrequencyPattern,
    InterestAreaDistribution, PreferenceTag, TimeRange, UserMemorySummary,
)

MIN_SUMMARY_COUNT = 5

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
        self.runtime = runtime
        if repository is not None:
            self.repository = repository
        elif runtime is not None:
            self.repository = ImplicitMemoryRepository(runtime.postgres, runtime.neo4j)
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

    def _build_empty_profile(self) -> dict:
        """Build the empty profile when the combined summary count is insufficient."""
        now_ms = to_timestamp_ms(utcnow_naive())
        insufficient = "Insufficient data for analysis"

        def _empty_dimension(name: str) -> dict:
            return {
                "evidence": [insufficient],
                "reasoning": f"No clear evidence found for {name} dimension",
                "percentage": 0.0,
                "dimension_name": name,
                "confidence_level": 20,
            }

        def _empty_category(name: str) -> dict:
            return {
                "evidence": [insufficient],
                "percentage": 25.0,
                "category_name": name,
                "trending_direction": None,
            }

        return {
            "habits": [],
            "portrait": {
                "aesthetic": _empty_dimension("aesthetic"),
                "creativity": _empty_dimension("creativity"),
                "literature": _empty_dimension("literature"),
                "technology": _empty_dimension("technology"),
                "historical_trends": None,
                "analysis_timestamp": now_ms,
                "total_summaries_analyzed": 0,
            },
            "preferences": [],
            "interest_areas": {
                "art": _empty_category("art"),
                "tech": _empty_category("tech"),
                "music": _empty_category("music"),
                "lifestyle": _empty_category("lifestyle"),
                "analysis_timestamp": now_ms,
                "total_summaries_analyzed": 0,
            },
        }

    async def initialize_for_user(self, *, end_user_id: str, workspace_id: UUID) -> None:
        from src.services.implicit_memory.analyzers.dimension_analyzer import DimensionAnalyzer
        from src.services.implicit_memory.analyzers.interest_analyzer import InterestAnalyzer
        from src.services.implicit_memory.analyzers.preference_analyzer import PreferenceAnalyzer
        from src.services.implicit_memory.data_source import MemoryDataSource
        from src.services.implicit_memory.habit_detector import HabitDetector
        from src.services.end_user import EndUserService
        from src.services.workspace import WorkspaceService

        user = await EndUserService.from_postgres(self.runtime.postgres).resolve_in_workspace(
            end_user_id, workspace_id
        )
        if user is None:
            raise ValueError("User not found in workspace")
        workspace = await WorkspaceService.from_postgres(self.runtime.postgres).row(workspace_id)
        self.end_user_id = end_user_id
        self.memory_config = await self._get_user_memory_config(workspace)
        # Extract LLM model ID from memory config
        llm_model_id = str(self.memory_config.llm_model_id) if self.memory_config.llm_model_id else None

        # Reuse the microservice-owned Neo4j lifecycle.
        self.neo4j_connector = self.runtime.neo4j

        # Initialize core components with LLM model ID
        tenant_id = getattr(self.memory_config, 'tenant_id', None)
        self.data_source = MemoryDataSource(self.repository)
        self.preference_analyzer = PreferenceAnalyzer(self.runtime, llm_model_id, tenant_id=tenant_id)
        self.dimension_analyzer = DimensionAnalyzer(self.runtime, llm_model_id, tenant_id=tenant_id)
        self.interest_analyzer = InterestAnalyzer(self.runtime, llm_model_id, tenant_id=tenant_id)
        self.habit_detector = HabitDetector(self.runtime, llm_model_id, tenant_id=tenant_id)
        logger.info(f"ImplicitMemoryService initialized for end_user: {end_user_id}")

    async def _get_user_memory_config(self, workspace) -> MemoryConfig:
        """Get memory configuration for the connected end user.
        
        Returns:
            MemoryConfig: User's connected memory configuration
            
        Raises:
            ValueError: If no memory configuration found for user
        """
        try:
            from src.services.memory_config.service import load_memory_config_async

            config_id = workspace["memory_config"]
            if not config_id:
                raise ValueError(f"空间{workspace['id']}无启用的记忆配置")
            memory_config, _ = await load_memory_config_async(
                self.runtime, config_id, workspace["id"], workspace["tenant_id"]
            )
            logger.info(f"Loaded memory config {config_id} for end_user: {self.end_user_id}")
            return memory_config
        except Exception as e:
            logger.error(f"Failed to get memory config for end_user {self.end_user_id}: {e}")
            raise ValueError(f"Unable to get memory configuration for end_user {self.end_user_id}: {e}")

    async def save_profile_cache(
        self,
        end_user_id: str,
        profile_data: dict,
        workspace_id: UUID,
        expires_hours: int = 168  # 保留参数以保持接口兼容性
    ) -> None:
        """保存用户画像到数据库
        
        Args:
            end_user_id: 终端用户ID
            profile_data: 画像数据
            db: 数据库会话
            expires_hours: 保留参数（兼容性）
        """
        try:
            logger.info(f"保存用户画像到数据库: user={end_user_id}")
            await self.repository.save_profile(end_user_id, workspace_id, profile_data)
            logger.info(f"用户画像保存成功: user={end_user_id}")
        except Exception as e:
            # The service-owned session rolls back before this boundary.
            logger.error(f"保存用户画像失败: {str(e)}", exc_info=True)

    async def extract_user_summaries(
        self,
        user_id: str,
        time_range: Optional[TimeRange] = None,
        limit: Optional[int] = None
    ) -> List[UserMemorySummary]:
        """Extract user-specific SceneSummary and MemorySummary records.
        
        Args:
            user_id: Target user ID
            time_range: Optional time range to filter summaries
            limit: Optional limit on number of summaries
            
        Returns:
            List of user-specific memory summaries
        """
        logger.info(f"Extracting user summary records for user {user_id}")
        
        try:
            summaries = await self.data_source.get_user_summaries(
                user_id=user_id,
                time_range=time_range,
                limit=limit or 1000
            )
            
            logger.info(f"Extracted {len(summaries)} summary records for user {user_id}")
            return summaries
            
        except Exception as e:
            logger.error(f"Failed to extract user summaries for user {user_id}: {e}")
            raise

    async def get_preference_tags(
        self,
        user_id: str,
        confidence_threshold: float = 0.5,
        tag_category: Optional[str] = None,
        date_range: Optional[DateRange] = None
    ) -> List[PreferenceTag]:
        """Retrieve user preference tags with filtering.
        
        Args:
            user_id: Target user ID
            confidence_threshold: Minimum confidence score for tags
            tag_category: Optional category filter
            date_range: Optional date range filter
            
        Returns:
            List of filtered preference tags
        """
        logger.info(f"Getting preference tags for user {user_id}")
        
        try:
            # Get user summaries for analysis
            time_range = None
            if date_range:
                time_range = TimeRange(
                    start_date=date_range.start_date or datetime.min,
                    end_date=date_range.end_date or utcnow_naive()
                )
            
            user_summaries = await self.extract_user_summaries(
                user_id=user_id,
                time_range=time_range
            )
            
            if not user_summaries:
                logger.warning(f"No summaries found for user {user_id}")
                return []
            
            # Analyze preferences
            preference_tags = await self.preference_analyzer.analyze_preferences(
                user_id=user_id,
                user_summaries=user_summaries
            )
            
            # Apply filters
            filtered_tags = []
            for tag in preference_tags:
                # Filter by confidence threshold
                if tag.confidence_score < confidence_threshold:
                    continue
                
                # Filter by category if specified
                if tag_category and tag.category != tag_category:
                    continue
                
                # Filter by date range if specified
                if date_range:
                    if date_range.start_date and tag.created_at < date_range.start_date:
                        continue
                    if date_range.end_date and tag.created_at > date_range.end_date:
                        continue
                
                filtered_tags.append(tag)
            
            # Sort by confidence score and recency
            filtered_tags.sort(
                key=lambda x: (x.confidence_score, x.updated_at),
                reverse=True
            )
            
            logger.info(f"Retrieved {len(filtered_tags)} preference tags for user {user_id}")
            return filtered_tags
            
        except Exception as e:
            logger.error(f"Failed to get preference tags for user {user_id}: {e}")
            raise

    async def get_dimension_portrait(
        self,
        user_id: str,
        include_history: bool = False
    ) -> DimensionPortrait:
        """Get user's four-dimension personality portrait.
        
        Args:
            user_id: Target user ID
            include_history: Whether to include historical trends
            
        Returns:
            User's dimension portrait
        """
        logger.info(f"Getting dimension portrait for user {user_id}")
        
        try:
            # Get user summaries
            user_summaries = await self.extract_user_summaries(user_id=user_id)
            
            if not user_summaries:
                logger.warning(f"No summaries found for user {user_id}")
                return self.dimension_analyzer._create_empty_portrait(user_id)
            
            # Analyze dimensions
            dimension_portrait = await self.dimension_analyzer.analyze_dimensions(
                user_id=user_id,
                user_summaries=user_summaries
            )
            
            # Include historical trends if requested
            if include_history:
                # In a full implementation, this would retrieve historical data
                # For now, we'll leave historical_trends as None
                pass
            
            logger.info(f"Retrieved dimension portrait for user {user_id}")
            return dimension_portrait
            
        except Exception as e:
            logger.error(f"Failed to get dimension portrait for user {user_id}: {e}")
            raise

    async def get_interest_area_distribution(
        self,
        user_id: str,
        include_trends: bool = False
    ) -> InterestAreaDistribution:
        """Get user's interest area distribution across four areas.
        
        Args:
            user_id: Target user ID
            include_trends: Whether to include trending information
            
        Returns:
            User's interest area distribution
        """
        logger.info(f"Getting interest area distribution for user {user_id}")
        
        try:
            # Get user summaries
            user_summaries = await self.extract_user_summaries(user_id=user_id)
            
            if not user_summaries:
                logger.warning(f"No summaries found for user {user_id}")
                return self.interest_analyzer._create_empty_distribution(user_id)
            
            # Analyze interests
            interest_distribution = await self.interest_analyzer.analyze_interests(
                user_id=user_id,
                user_summaries=user_summaries
            )
            
            # Include trends if requested
            if include_trends:
                # In a full implementation, this would calculate trending directions
                # For now, we'll leave trending_direction as None for each category
                pass
            
            logger.info(f"Retrieved interest area distribution for user {user_id}")
            return interest_distribution
            
        except Exception as e:
            logger.error(f"Failed to get interest area distribution for user {user_id}: {e}")
            raise

    async def get_behavior_habits(
        self,
        user_id: str,
        confidence_level: Optional[int] = None,
        frequency_pattern: Optional[str] = None,
        time_period: Optional[str] = None
    ) -> List[BehaviorHabit]:
        """Get user's behavioral habits with filtering.
        
        Args:
            user_id: Target user ID
            confidence_level: Optional confidence level filter (0-100)
            frequency_pattern: Optional frequency pattern filter
            time_period: Optional time period filter ("current", "past")
            
        Returns:
            List of filtered behavioral habits
        """
        logger.info(f"Getting behavior habits for user {user_id}")
        
        try:
            # Get user summaries
            user_summaries = await self.extract_user_summaries(user_id=user_id)
            
            if not user_summaries:
                logger.warning(f"No summaries found for user {user_id}")
                return []
            
            # Detect habits
            behavior_habits = await self.habit_detector.detect_habits(
                user_id=user_id,
                user_summaries=user_summaries
            )
            
            # Apply filters
            filtered_habits = []
            for habit in behavior_habits:
                # Filter by confidence level
                if confidence_level is not None:
                    if habit.confidence_level < confidence_level:
                        continue
                
                # Filter by frequency pattern
                if frequency_pattern:
                    try:
                        target_frequency = FrequencyPattern(frequency_pattern.lower())
                        if habit.frequency_pattern != target_frequency:
                            continue
                    except ValueError:
                        logger.warning(f"Invalid frequency pattern: {frequency_pattern}")
                        continue
                
                # Filter by time period
                if time_period:
                    if time_period.lower() == "current" and not habit.is_current:
                        continue
                    elif time_period.lower() == "past" and habit.is_current:
                        continue
                
                filtered_habits.append(habit)
            
            # Sort by confidence level and recency
            filtered_habits.sort(
                key=lambda x: (x.confidence_level, x.last_observed),
                reverse=True
            )
            
            logger.info(f"Retrieved {len(filtered_habits)} behavior habits for user {user_id}")
            return filtered_habits
            
        except Exception as e:
            logger.error(f"Failed to get behavior habits for user {user_id}: {e}")
            raise

    async def generate_complete_profile(
        self,
        user_id: str
    ) -> dict:
        """生成完整的用户画像（包含所有4个模块）
        
        The combined SceneSummary and MemorySummary count must be at least 5.
        否则返回固定的空白画像数据。
        
        Args:
            user_id: 用户ID
            
        Returns:
            Dict: 包含所有模块的完整画像数据
        """
        logger.info(f"生成完整用户画像: user={user_id}")
        
        try:
            summary_count = await self.data_source.count_user_summaries(
                user_id=user_id
            )
            logger.info(
                "用户 SceneSummary 和 MemorySummary 总数量: %s (user=%s)",
                summary_count,
                user_id,
            )

            if summary_count < MIN_SUMMARY_COUNT:
                logger.info(
                    "摘要总数量不足 %s（当前 %s），返回空白画像: user=%s",
                    MIN_SUMMARY_COUNT,
                    summary_count,
                    user_id,
                )
                return self._build_empty_profile()

            # Run the four analysis methods in parallel.
            preferences, portrait, interest_areas, habits = await asyncio.gather(
                self.get_preference_tags(user_id=user_id),
                self.get_dimension_portrait(user_id=user_id),
                self.get_interest_area_distribution(user_id=user_id),
                self.get_behavior_habits(user_id=user_id),
            )
            
            # 转换为可序列化的格式
            profile_data = {
                "preferences": [tag.model_dump(mode='json') for tag in preferences],
                "portrait": portrait.model_dump(mode='json'),
                "interest_areas": interest_areas.model_dump(mode='json'),
                "habits": [habit.model_dump(mode='json') for habit in habits]
            }
            
            logger.info(f"完整用户画像生成完成: user={user_id}")
            return profile_data
            
        except Exception as e:
            logger.error(f"生成完整用户画像失败: {str(e)}", exc_info=True)
            raise
