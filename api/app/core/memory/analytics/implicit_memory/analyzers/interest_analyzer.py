"""Interest Analyzer for Implicit Memory System

This module implements LLM-based interest area analysis from user memory summaries.
It categorizes user interests into four areas: tech, lifestyle, music, and art,
providing percentage distribution that totals 100%.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.core.memory.analytics.implicit_memory.llm_client import ImplicitMemoryLLMClient
from app.core.memory.llm_tools.llm_client import LLMClientException
from app.schemas.implicit_memory_schema import (
    InterestAreaDistribution,
    InterestCategory,
    UserMemorySummary,
)
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class InterestData(BaseModel):
    """Individual interest category analysis data."""
    percentage: float = Field(ge=0.0, le=100.0)
    evidence: List[str] = Field(default_factory=list)
    trending_direction: Optional[str] = None


class InterestAnalysisResponse(BaseModel):
    """Response model for interest analysis."""
    interest_distribution: Dict[str, InterestData] = Field(default_factory=dict)


class InterestAnalyzer:
    """Analyzes user memory summaries to extract interest area distribution."""
    
    # Define the four interest categories we analyze
    INTEREST_CATEGORIES = ["tech", "lifestyle", "music", "art"]
    
    def __init__(self, db: Session, llm_model_id: Optional[str] = None):
        """Initialize the interest analyzer.
        
        Args:
            db: Database session
            llm_model_id: Optional LLM model ID to use for analysis
        """
        self.db = db
        self.llm_model_id = llm_model_id
        self._llm_client = ImplicitMemoryLLMClient(db, llm_model_id)
    
    async def analyze_interests(
        self,
        user_id: str,
        user_summaries: List[UserMemorySummary],
        existing_distribution: Optional[InterestAreaDistribution] = None
    ) -> InterestAreaDistribution:
        """Analyze user summaries to extract interest area distribution.
        
        Args:
            user_id: Target user ID
            user_summaries: List of user-specific memory summaries
            existing_distribution: Optional existing distribution for trend tracking
            
        Returns:
            Interest area distribution across four categories
            
        Raises:
            LLMClientException: If LLM analysis fails
        """
        if not user_summaries:
            logger.warning(f"No summaries provided for user {user_id}")
            return self._create_empty_distribution(user_id)
        
        try:
            logger.info(f"Analyzing interests for user {user_id} with {len(user_summaries)} summaries")
            
            # Use the LLM client wrapper for analysis
            response = await self._llm_client.analyze_interests(
                user_summaries=user_summaries,
                user_id=user_id,
                model_id=self.llm_model_id
            )
            
            # Create interest categories
            interest_categories = {}
            current_time = datetime.now()
            
            # Extract interest_distribution from response dict
            interest_distribution = response.get("interest_distribution", {})
            
            # Extract and validate interest data
            raw_interests = {}
            for category_name in self.INTEREST_CATEGORIES:
                interest_data_dict = interest_distribution.get(category_name)
                if interest_data_dict:
                    raw_interests[category_name] = InterestData(
                        percentage=interest_data_dict.get("percentage", 0.0),
                        evidence=interest_data_dict.get("evidence", []),
                        trending_direction=interest_data_dict.get("trending_direction")
                    )
                else:
                    # Create default if missing
                    logger.warning(f"Missing interest data for {category_name}, using default")
                    raw_interests[category_name] = InterestData(
                        percentage=0.0,
                        evidence=["No specific evidence found"],
                        trending_direction=None
                    )
            
            # Normalize percentages to ensure they sum to 100%
            normalized_interests = self._normalize_percentages(raw_interests)
            
            # Create interest category objects
            for category_name in self.INTEREST_CATEGORIES:
                interest_data = normalized_interests[category_name]
                
                # Calculate trending direction if we have existing data
                trending_direction = self._calculate_trending_direction(
                    category_name=category_name,
                    current_percentage=interest_data.percentage,
                    existing_distribution=existing_distribution
                ) if existing_distribution else interest_data.trending_direction
                
                interest_categories[category_name] = InterestCategory(
                    category_name=category_name,
                    percentage=interest_data.percentage,
                    evidence=interest_data.evidence if interest_data.evidence else ["No specific evidence found"],
                    trending_direction=trending_direction
                )
            
            # Create interest area distribution
            distribution = InterestAreaDistribution(
                user_id=user_id,
                tech=interest_categories["tech"],
                lifestyle=interest_categories["lifestyle"],
                music=interest_categories["music"],
                art=interest_categories["art"],
                analysis_timestamp=current_time,
                total_summaries_analyzed=len(user_summaries)
            )
            
            # Validate that percentages sum to 100%
            total_percentage = distribution.total_percentage
            if not (99.9 <= total_percentage <= 100.1):
                logger.warning(f"Interest percentages sum to {total_percentage}, expected ~100%")
            
            logger.info(f"Created interest distribution for user {user_id}")
            return distribution
            
        except LLMClientException:
            raise
        except Exception as e:
            logger.error(f"Interest analysis failed for user {user_id}: {e}")
            raise LLMClientException(f"Interest analysis failed: {e}") from e
    
    def _normalize_percentages(self, raw_interests: Dict[str, InterestData]) -> Dict[str, InterestData]:
        """Normalize percentages to ensure they sum to 100%.
        
        Args:
            raw_interests: Raw interest data with potentially unnormalized percentages
            
        Returns:
            Normalized interest data
        """
        # Calculate current total
        total = sum(interest.percentage for interest in raw_interests.values())
        
        if total == 0:
            # If all percentages are 0, distribute equally
            equal_percentage = 100.0 / len(self.INTEREST_CATEGORIES)
            normalized = {}
            for category_name, interest_data in raw_interests.items():
                normalized[category_name] = InterestData(
                    percentage=equal_percentage,
                    evidence=interest_data.evidence,
                    trending_direction=interest_data.trending_direction
                )
            return normalized
        
        # Normalize to sum to 100%
        normalization_factor = 100.0 / total
        normalized = {}
        
        for category_name, interest_data in raw_interests.items():
            normalized_percentage = interest_data.percentage * normalization_factor
            
            normalized[category_name] = InterestData(
                percentage=round(normalized_percentage, 1),
                evidence=interest_data.evidence,
                trending_direction=interest_data.trending_direction
            )
        
        # Handle rounding errors by adjusting the largest category
        current_total = sum(interest.percentage for interest in normalized.values())
        if abs(current_total - 100.0) > 0.1:
            # Find category with largest percentage and adjust
            largest_category = max(normalized.keys(), key=lambda k: normalized[k].percentage)
            adjustment = 100.0 - current_total
            
            adjusted_percentage = normalized[largest_category].percentage + adjustment
            normalized[largest_category] = InterestData(
                percentage=round(max(0.0, adjusted_percentage), 1),
                evidence=normalized[largest_category].evidence,
                trending_direction=normalized[largest_category].trending_direction
            )
        
        return normalized
    
    def _calculate_trending_direction(
        self,
        category_name: str,
        current_percentage: float,
        existing_distribution: InterestAreaDistribution,
        threshold: float = 5.0
    ) -> Optional[str]:
        """Calculate trending direction for an interest category.
        
        Args:
            category_name: Name of the interest category
            current_percentage: Current percentage for the category
            existing_distribution: Previous distribution for comparison
            threshold: Minimum percentage change to consider a trend
            
        Returns:
            Trending direction: "increasing", "decreasing", "stable", or None
        """
        try:
            # Get previous percentage
            previous_category = getattr(existing_distribution, category_name, None)
            if not previous_category:
                return None
            
            previous_percentage = previous_category.percentage
            change = current_percentage - previous_percentage
            
            if abs(change) < threshold:
                return "stable"
            elif change > 0:
                return "increasing"
            else:
                return "decreasing"
                
        except Exception as e:
            logger.error(f"Error calculating trending direction for {category_name}: {e}")
            return None
    
    def _create_empty_distribution(self, user_id: str) -> InterestAreaDistribution:
        """Create an empty interest distribution when no data is available.
        
        Args:
            user_id: Target user ID
            
        Returns:
            Empty InterestAreaDistribution with equal percentages
        """
        current_time = datetime.now()
        equal_percentage = 25.0  # 100% / 4 categories
        
        default_category = lambda name: InterestCategory(
            category_name=name,
            percentage=equal_percentage,
            evidence=["Insufficient data for analysis"],
            trending_direction=None
        )
        
        return InterestAreaDistribution(
            user_id=user_id,
            tech=default_category("tech"),
            lifestyle=default_category("lifestyle"),
            music=default_category("music"),
            art=default_category("art"),
            analysis_timestamp=current_time,
            total_summaries_analyzed=0
        )