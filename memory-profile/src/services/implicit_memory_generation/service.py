"""Generate the legacy implicit profile with profile-owned infrastructure."""

import asyncio
import logging
from datetime import datetime
from uuid import UUID

from src.common.timing import to_timestamp_ms, utcnow_naive
from src.repositories.implicit_memory_generation import ImplicitProfileGenerationRepository
from src.runtime import ProfileRuntime
from src.schemas.implicit_memory_generation import (
    BehaviorHabit,
    DateRange,
    DimensionPortrait,
    FrequencyPattern,
    InterestAreaDistribution,
    PreferenceTag,
    TimeRange,
    UserMemorySummary,
)
from src.services.end_user import EndUserService
from src.services.implicit_memory_generation.analyzers.dimension_analyzer import DimensionAnalyzer
from src.services.implicit_memory_generation.analyzers.interest_analyzer import InterestAnalyzer
from src.services.implicit_memory_generation.analyzers.preference_analyzer import PreferenceAnalyzer
from src.services.implicit_memory_generation.config import load_generation_models
from src.services.implicit_memory_generation.data_source import MemoryDataSource
from src.services.implicit_memory_generation.habit_detector import HabitDetector

MIN_SUMMARY_COUNT = 5
logger = logging.getLogger(__name__)


class ImplicitMemoryGenerationService:
    def __init__(self, runtime: ProfileRuntime, model_id: str, tenant_id: UUID):
        self.data_source = MemoryDataSource(runtime.neo4j)
        self.repository = ImplicitProfileGenerationRepository(runtime.postgres)
        self.preference_analyzer = PreferenceAnalyzer(
            runtime.model_service, model_id, tenant_id=tenant_id
        )
        self.dimension_analyzer = DimensionAnalyzer(
            runtime.model_service, model_id, tenant_id=tenant_id
        )
        self.interest_analyzer = InterestAnalyzer(
            runtime.model_service, model_id, tenant_id=tenant_id
        )
        self.habit_detector = HabitDetector(runtime.model_service, model_id, tenant_id=tenant_id)

    @classmethod
    async def create(cls, runtime: ProfileRuntime, end_user_id: str, workspace_id: UUID):
        try:
            UUID(end_user_id)
            user = await EndUserService.from_postgres(runtime.postgres).resolve_in_workspace(
                end_user_id, workspace_id
            )
            if user is None:
                raise ValueError(f"end_user {end_user_id} 不存在")
            model_id, tenant_id = await load_generation_models(runtime, user.workspace_id)
        except Exception as exc:
            raise ValueError(
                f"Unable to get memory configuration for end_user {end_user_id}: {exc}"
            ) from exc
        return cls(runtime, str(model_id), tenant_id)

    async def extract_user_summaries(
        self, user_id: str, time_range: TimeRange | None = None, limit: int | None = None
    ) -> list[UserMemorySummary]:
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
                user_id=user_id, time_range=time_range, limit=limit or 1000
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
        tag_category: str | None = None,
        date_range: DateRange | None = None,
    ) -> list[PreferenceTag]:
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
                    end_date=date_range.end_date or utcnow_naive(),
                )

            user_summaries = await self.extract_user_summaries(
                user_id=user_id, time_range=time_range
            )

            if not user_summaries:
                logger.warning(f"No summaries found for user {user_id}")
                return []

            # Analyze preferences
            preference_tags = await self.preference_analyzer.analyze_preferences(
                user_id=user_id, user_summaries=user_summaries
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
            filtered_tags.sort(key=lambda x: (x.confidence_score, x.updated_at), reverse=True)

            logger.info(f"Retrieved {len(filtered_tags)} preference tags for user {user_id}")
            return filtered_tags

        except Exception as e:
            logger.error(f"Failed to get preference tags for user {user_id}: {e}")
            raise

    async def get_dimension_portrait(
        self, user_id: str, include_history: bool = False
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
                user_id=user_id, user_summaries=user_summaries
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
        self, user_id: str, include_trends: bool = False
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
                user_id=user_id, user_summaries=user_summaries
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
        confidence_level: int | None = None,
        frequency_pattern: str | None = None,
        time_period: str | None = None,
    ) -> list[BehaviorHabit]:
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
                user_id=user_id, user_summaries=user_summaries
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
            filtered_habits.sort(key=lambda x: (x.confidence_level, x.last_observed), reverse=True)

            logger.info(f"Retrieved {len(filtered_habits)} behavior habits for user {user_id}")
            return filtered_habits

        except Exception as e:
            logger.error(f"Failed to get behavior habits for user {user_id}: {e}")
            raise

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

    async def generate_complete_profile(self, user_id: str) -> dict:
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
            summary_count = await self.data_source.count_user_summaries(user_id=user_id)
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
                "preferences": [tag.model_dump(mode="json") for tag in preferences],
                "portrait": portrait.model_dump(mode="json"),
                "interest_areas": interest_areas.model_dump(mode="json"),
                "habits": [habit.model_dump(mode="json") for habit in habits],
            }

            logger.info(f"完整用户画像生成完成: user={user_id}")
            return profile_data

        except Exception as e:
            logger.error(f"生成完整用户画像失败: {str(e)}", exc_info=True)
            raise

    async def save_profile_cache(self, end_user_id: str, profile_data: dict) -> None:
        try:
            await self.repository.save_profile(end_user_id, profile_data)
        except Exception as exc:
            # Preserve the original save_profile_cache rollback/log-only behavior.
            logger.error(f"保存用户画像失败: {str(exc)}", exc_info=True)
