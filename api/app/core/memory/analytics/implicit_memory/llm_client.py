"""LLM Client Wrapper for Implicit Memory Analysis

This module provides a specialized LLM client wrapper that integrates with the
MemoryClientFactory to perform implicit memory analysis tasks including preference
extraction, personality dimension analysis, interest categorization, and habit detection.
"""

import logging
from typing import Any, Dict, List, Optional

from app.core.memory.analytics.implicit_memory.prompts import (
    get_dimension_analysis_prompt,
    get_habit_analysis_prompt,
    get_interest_analysis_prompt,
    get_preference_analysis_prompt,
)
from app.core.memory.llm_tools.llm_client import LLMClientException
from app.core.memory.utils.llm.llm_utils import MemoryClientFactory
from app.schemas.implicit_memory_schema import UserMemorySummary
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# Response Models for LLM Analysis

class PreferenceAnalysisResponse(BaseModel):
    """Response model for preference analysis."""
    preferences: List[Dict[str, Any]] = Field(default_factory=list)


class DimensionAnalysisResponse(BaseModel):
    """Response model for dimension analysis."""
    dimensions: Dict[str, Dict[str, Any]] = Field(default_factory=dict)


class InterestAnalysisResponse(BaseModel):
    """Response model for interest analysis."""
    interest_distribution: Dict[str, Dict[str, Any]] = Field(default_factory=dict)


class HabitAnalysisResponse(BaseModel):
    """Response model for habit analysis."""
    habits: List[Dict[str, Any]] = Field(default_factory=list)


class ImplicitMemoryLLMClient:
    """LLM client wrapper for implicit memory analysis.
    
    This class provides a high-level interface for performing LLM-based analysis
    of user memory summaries to extract preferences, personality dimensions,
    interests, and behavioral habits.
    """

    def __init__(self, db: Session, default_model_id: Optional[str] = None):
        """Initialize the LLM client wrapper.
        
        Args:
            db: Database session for accessing model configurations
            default_model_id: Default LLM model ID to use if none specified
        """
        self.db = db
        self.default_model_id = default_model_id
        self._client_factory = MemoryClientFactory(db)
        
        logger.info("ImplicitMemoryLLMClient initialized")

    def _get_llm_client(self, model_id: Optional[str] = None):
        """Get LLM client instance.
        
        Args:
            model_id: LLM model ID to use, defaults to default_model_id
            
        Returns:
            LLM client instance
            
        Raises:
            ValueError: If no model ID is provided and no default is set
            LLMClientException: If client creation fails
        """
        effective_model_id = model_id or self.default_model_id
        if not effective_model_id:
            raise ValueError("No LLM model ID provided and no default model ID set")
        
        try:
            client = self._client_factory.get_llm_client(effective_model_id)
            logger.debug(f"Created LLM client for model: {effective_model_id}")
            return client
        except Exception as e:
            logger.error(f"Failed to create LLM client for model {effective_model_id}: {e}")
            raise LLMClientException(f"Failed to create LLM client: {e}") from e

    def _prepare_summaries_for_analysis(self, user_summaries: List[UserMemorySummary]) -> List[Dict[str, Any]]:
        """Prepare user memory summaries for LLM analysis.
        
        Args:
            user_summaries: List of user memory summaries
            
        Returns:
            List of formatted summary dictionaries
        """
        formatted_summaries = []
        for summary in user_summaries:
            formatted_summary = {
                'summary_id': summary.summary_id,
                'user_content': summary.user_content,
                'timestamp': summary.timestamp.isoformat(),
                'summary_type': summary.summary_type,
                'confidence_score': summary.confidence_score
            }
            formatted_summaries.append(formatted_summary)
        
        logger.debug(f"Prepared {len(formatted_summaries)} summaries for analysis")
        return formatted_summaries

    async def analyze_preferences(
        self,
        user_summaries: List[UserMemorySummary],
        user_id: str,
        model_id: Optional[str] = None
    ) -> Dict[str, Any]:
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
            response = await llm_client.response_structured(
                messages=messages,
                response_model=PreferenceAnalysisResponse
            )
            
            result = response.model_dump()
            logger.info(f"Analyzed preferences for user {user_id}: found {len(result.get('preferences', []))} preferences")
            return result
            
        except Exception as e:
            logger.error(f"Preference analysis failed for user {user_id}: {e}")
            raise LLMClientException(f"Preference analysis failed: {e}") from e

    async def analyze_dimensions(
        self,
        user_summaries: List[UserMemorySummary],
        user_id: str,
        model_id: Optional[str] = None
    ) -> Dict[str, Any]:
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
            response = await llm_client.response_structured(
                messages=messages,
                response_model=DimensionAnalysisResponse
            )
            
            result = response.model_dump()
            dimensions = result.get('dimensions', {})
            logger.info(f"Analyzed dimensions for user {user_id}: {list(dimensions.keys())}")
            return result
            
        except Exception as e:
            logger.error(f"Dimension analysis failed for user {user_id}: {e}")
            raise LLMClientException(f"Dimension analysis failed: {e}") from e

    async def analyze_interests(
        self,
        user_summaries: List[UserMemorySummary],
        user_id: str,
        model_id: Optional[str] = None
    ) -> Dict[str, Any]:
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
            response = await llm_client.response_structured(
                messages=messages,
                response_model=InterestAnalysisResponse
            )
            
            result = response.model_dump()
            interest_dist = result.get('interest_distribution', {})
            logger.info(f"Analyzed interests for user {user_id}: {list(interest_dist.keys())}")
            return result
            
        except Exception as e:
            logger.error(f"Interest analysis failed for user {user_id}: {e}")
            raise LLMClientException(f"Interest analysis failed: {e}") from e

    async def analyze_habits(
        self,
        user_summaries: List[UserMemorySummary],
        user_id: str,
        model_id: Optional[str] = None
    ) -> Dict[str, Any]:
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
            response = await llm_client.response_structured(
                messages=messages,
                response_model=HabitAnalysisResponse
            )
            
            result = response.model_dump()
            logger.info(f"Analyzed habits for user {user_id}: found {len(result.get('habits', []))} habits")
            return result
            
        except Exception as e:
            logger.error(f"Habit analysis failed for user {user_id}: {e}")
            raise LLMClientException(f"Habit analysis failed: {e}") from e