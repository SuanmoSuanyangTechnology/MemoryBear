"""LLM Client Wrapper for Implicit Memory Analysis

This module provides a specialized LLM client wrapper that integrates with the
ModelClientMixin to perform implicit memory analysis tasks including preference
extraction, personality dimension analysis, interest categorization, and habit detection.
"""

import logging
from typing import Any

from pydantic import BaseModel, Field

from src.common.datetime_utils import to_iso_z
from src.services.implicit_memory.errors import LLMClientException
from src.services.implicit_memory.prompts import (
    get_dimension_analysis_prompt,
    get_habit_analysis_prompt,
    get_interest_analysis_prompt,
    get_preference_analysis_prompt,
)
from src.schemas.implicit_memory import UserMemorySummary

logger = logging.getLogger(__name__)


# Response Models for LLM Analysis


class PreferenceAnalysisResponse(BaseModel):
    """Response model for preference analysis."""

    preferences: list[dict[str, Any]] = Field(default_factory=list)


class DimensionAnalysisResponse(BaseModel):
    """Response model for dimension analysis."""

    dimensions: dict[str, dict[str, Any]] = Field(default_factory=dict)


class InterestAnalysisResponse(BaseModel):
    """Response model for interest analysis."""

    interest_distribution: dict[str, dict[str, Any]] = Field(default_factory=dict)


class HabitAnalysisResponse(BaseModel):
    """Response model for habit analysis."""

    habits: list[dict[str, Any]] = Field(default_factory=list)


class ImplicitMemoryLLMClient:
    """LLM client wrapper for implicit memory analysis.

    This class provides a high-level interface for performing LLM-based analysis
    of user memory summaries to extract preferences, personality dimensions,
    interests, and behavioral habits.
    """

    def __init__(self, model):
        self.model = model

    def _get_llm_client(self, model_id=None):
        return self.model

    def _prepare_summaries_for_analysis(
        self, user_summaries: list[UserMemorySummary]
    ) -> list[dict[str, Any]]:
        """Prepare user memory summaries for LLM analysis.

        Args:
            user_summaries: List of user memory summaries

        Returns:
            List of formatted summary dictionaries
        """
        formatted_summaries = []
        for summary in user_summaries:
            formatted_summary = {
                "summary_id": summary.summary_id,
                "user_content": summary.user_content,
                "timestamp": to_iso_z(summary.timestamp),
                "summary_type": summary.summary_type,
                "confidence_score": summary.confidence_score,
            }
            formatted_summaries.append(formatted_summary)

        logger.debug(f"Prepared {len(formatted_summaries)} summaries for analysis")
        return formatted_summaries

    async def analyze_preferences(
        self, user_summaries: list[UserMemorySummary], user_id: str, model_id: str | None = None
    ) -> dict[str, Any]:
        """Analyze user preferences from memory summaries.

        Args:
            user_summaries: List of user memory summaries to analyze
            user_id: Target user ID for analysis
            model_id: Optional LLM model ID to use

        Returns:
            Dictionary containing extracted preferences

        Raises:
            LLMClientException: If LLM analysis fails
            ValueError: If input validation fails
        """
        if not user_summaries:
            logger.warning(f"No summaries provided for preference analysis of user {user_id}")
            return {"preferences": []}

        if not user_id:
            raise ValueError("User ID is required for preference analysis")

        try:
            # Prepare summaries and get prompt
            formatted_summaries = self._prepare_summaries_for_analysis(user_summaries)
            prompt = get_preference_analysis_prompt(formatted_summaries, user_id)

            # Get LLM client and perform analysis
            llm_client = self._get_llm_client(model_id)

            messages = [{"role": "user", "content": prompt}]

            # Use structured output for reliable parsing
            response = await llm_client.call_structured(messages, PreferenceAnalysisResponse)

            result = response.model_dump()
            logger.info(
                f"Analyzed preferences for user {user_id}: found {len(result.get('preferences', []))} preferences"
            )
            return result

        except Exception as e:
            logger.error(f"Preference analysis failed for user {user_id}: {e}")
            raise LLMClientException(f"Preference analysis failed: {e}") from e

    async def analyze_dimensions(
        self, user_summaries: list[UserMemorySummary], user_id: str, model_id: str | None = None
    ) -> dict[str, Any]:
        """Analyze user personality dimensions from memory summaries.

        Args:
            user_summaries: List of user memory summaries to analyze
            user_id: Target user ID for analysis
            model_id: Optional LLM model ID to use

        Returns:
            Dictionary containing dimension scores and analysis

        Raises:
            LLMClientException: If LLM analysis fails
            ValueError: If input validation fails
        """
        if not user_summaries:
            logger.warning(f"No summaries provided for dimension analysis of user {user_id}")
            return {"dimensions": {}}

        if not user_id:
            raise ValueError("User ID is required for dimension analysis")

        try:
            # Prepare summaries and get prompt
            formatted_summaries = self._prepare_summaries_for_analysis(user_summaries)
            prompt = get_dimension_analysis_prompt(formatted_summaries, user_id)

            # Get LLM client and perform analysis
            llm_client = self._get_llm_client(model_id)

            messages = [{"role": "user", "content": prompt}]

            # Use structured output for reliable parsing
            response = await llm_client.call_structured(messages, DimensionAnalysisResponse)

            result = response.model_dump()
            dimensions = result.get("dimensions", {})
            logger.info(f"Analyzed dimensions for user {user_id}: {list(dimensions.keys())}")
            return result

        except Exception as e:
            logger.error(f"Dimension analysis failed for user {user_id}: {e}")
            raise LLMClientException(f"Dimension analysis failed: {e}") from e

    async def analyze_interests(
        self, user_summaries: list[UserMemorySummary], user_id: str, model_id: str | None = None
    ) -> dict[str, Any]:
        """Analyze user interest distribution from memory summaries.

        Args:
            user_summaries: List of user memory summaries to analyze
            user_id: Target user ID for analysis
            model_id: Optional LLM model ID to use

        Returns:
            Dictionary containing interest area distribution

        Raises:
            LLMClientException: If LLM analysis fails
            ValueError: If input validation fails
        """
        if not user_summaries:
            logger.warning(f"No summaries provided for interest analysis of user {user_id}")
            return {"interest_distribution": {}}

        if not user_id:
            raise ValueError("User ID is required for interest analysis")

        try:
            # Prepare summaries and get prompt
            formatted_summaries = self._prepare_summaries_for_analysis(user_summaries)
            prompt = get_interest_analysis_prompt(formatted_summaries, user_id)

            # Get LLM client and perform analysis
            llm_client = self._get_llm_client(model_id)

            messages = [{"role": "user", "content": prompt}]

            # Use structured output for reliable parsing
            response = await llm_client.call_structured(messages, InterestAnalysisResponse)

            result = response.model_dump()
            interest_dist = result.get("interest_distribution", {})
            logger.info(f"Analyzed interests for user {user_id}: {list(interest_dist.keys())}")
            return result

        except Exception as e:
            logger.error(f"Interest analysis failed for user {user_id}: {e}")
            raise LLMClientException(f"Interest analysis failed: {e}") from e

    async def analyze_habits(
        self, user_summaries: list[UserMemorySummary], user_id: str, model_id: str | None = None
    ) -> dict[str, Any]:
        """Analyze user behavioral habits from memory summaries.

        Args:
            user_summaries: List of user memory summaries to analyze
            user_id: Target user ID for analysis
            model_id: Optional LLM model ID to use

        Returns:
            Dictionary containing identified behavioral habits

        Raises:
            LLMClientException: If LLM analysis fails
            ValueError: If input validation fails
        """
        if not user_summaries:
            logger.warning(f"No summaries provided for habit analysis of user {user_id}")
            return {"habits": []}

        if not user_id:
            raise ValueError("User ID is required for habit analysis")

        try:
            # Prepare summaries and get prompt
            formatted_summaries = self._prepare_summaries_for_analysis(user_summaries)
            prompt = get_habit_analysis_prompt(formatted_summaries, user_id)

            # Get LLM client and perform analysis
            llm_client = self._get_llm_client(model_id)

            messages = [{"role": "user", "content": prompt}]

            # Use structured output for reliable parsing
            response = await llm_client.call_structured(messages, HabitAnalysisResponse)

            result = response.model_dump()
            logger.info(
                f"Analyzed habits for user {user_id}: found {len(result.get('habits', []))} habits"
            )
            return result

        except Exception as e:
            logger.error(f"Habit analysis failed for user {user_id}: {e}")
            raise LLMClientException(f"Habit analysis failed: {e}") from e
