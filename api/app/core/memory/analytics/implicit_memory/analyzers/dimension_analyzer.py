"""Dimension Analyzer for Implicit Memory System

This module implements LLM-based personality dimension analysis from user memory summaries.
It analyzes four key dimensions: creativity, aesthetic, technology, and literature,
providing percentage scores with evidence and reasoning.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.core.memory.analytics.implicit_memory.llm_client import ImplicitMemoryLLMClient
from app.core.memory.llm_tools.llm_client import LLMClientException
from app.schemas.implicit_memory_schema import (
    DimensionPortrait,
    DimensionScore,
    UserMemorySummary,
)
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class DimensionData(BaseModel):
    """Individual dimension analysis data."""
    percentage: float = Field(ge=0.0, le=100.0)
    evidence: List[str] = Field(default_factory=list)
    reasoning: str = ""
    confidence_level: int = 50  # Default to medium confidence


class DimensionAnalysisResponse(BaseModel):
    """Response model for dimension analysis."""
    dimensions: Dict[str, DimensionData] = Field(default_factory=dict)


class DimensionAnalyzer:
    """Analyzes user memory summaries to extract personality dimensions."""
    
    # Define the four dimensions we analyze
    DIMENSIONS = ["creativity", "aesthetic", "technology", "literature"]
    
    def __init__(self, db: Session, llm_model_id: Optional[str] = None):
        """Initialize the dimension analyzer.
        
        Args:
            db: Database session
            llm_model_id: Optional LLM model ID to use for analysis
        """
        self.db = db
        self.llm_model_id = llm_model_id
        self._llm_client = ImplicitMemoryLLMClient(db, llm_model_id)
    
    async def analyze_dimensions(
        self,
        user_id: str,
        user_summaries: List[UserMemorySummary],
        existing_portrait: Optional[DimensionPortrait] = None
    ) -> DimensionPortrait:
        """Analyze user summaries to extract personality dimensions.
        
        Args:
            user_id: Target user ID
            user_summaries: List of user-specific memory summaries
            existing_portrait: Optional existing portrait for incremental updates
            
        Returns:
            Dimension portrait with four personality dimensions
            
        Raises:
            LLMClientException: If LLM analysis fails
        """
        if not user_summaries:
            logger.warning(f"No summaries provided for user {user_id}")
            return self._create_empty_portrait(user_id)
        
        try:
            logger.info(f"Analyzing dimensions for user {user_id} with {len(user_summaries)} summaries")
            
            # Use the LLM client wrapper for analysis
            response = await self._llm_client.analyze_dimensions(
                user_summaries=user_summaries,
                user_id=user_id,
                model_id=self.llm_model_id
            )
            
            # Create dimension scores
            dimension_scores = {}
            current_time = datetime.now()
            
            for dimension_name in self.DIMENSIONS:
                # Handle response as dictionary
                dimensions_data = response.get("dimensions", {})
                dimension_data = dimensions_data.get(dimension_name)
                
                if dimension_data:
                    # Validate and create dimension score
                    score = self._create_dimension_score(
                        dimension_name=dimension_name,
                        dimension_data=dimension_data
                    )
                    dimension_scores[dimension_name] = score
                else:
                    # Create default score if missing
                    logger.warning(f"Missing dimension data for {dimension_name}, using default")
                    dimension_scores[dimension_name] = self._create_default_dimension_score(dimension_name)
            
            # Create dimension portrait
            portrait = DimensionPortrait(
                user_id=user_id,
                creativity=dimension_scores["creativity"],
                aesthetic=dimension_scores["aesthetic"],
                technology=dimension_scores["technology"],
                literature=dimension_scores["literature"],
                analysis_timestamp=current_time,
                total_summaries_analyzed=len(user_summaries),
                historical_trends=self._calculate_historical_trends(existing_portrait) if existing_portrait else None
            )
            
            logger.info(f"Created dimension portrait for user {user_id}")
            return portrait
            
        except LLMClientException:
            raise
        except Exception as e:
            logger.error(f"Dimension analysis failed for user {user_id}: {e}")
            raise LLMClientException(f"Dimension analysis failed: {e}") from e
    
    def _create_dimension_score(
        self,
        dimension_name: str,
        dimension_data: dict
    ) -> DimensionScore:
        """Create a dimension score from analysis data.
        
        Args:
            dimension_name: Name of the dimension
            dimension_data: Analysis data dictionary for the dimension
            
        Returns:
            DimensionScore object
        """
        # Validate percentage - handle dict access
        percentage = dimension_data.get("percentage", 0.0)
        percentage = max(0.0, min(100.0, float(percentage)))
        
        # Validate confidence level
        confidence_level = self._validate_confidence_level(dimension_data.get("confidence_level", 50))
        
        # Ensure evidence is not empty
        evidence = dimension_data.get("evidence", [])
        if not evidence:
            evidence = ["No specific evidence found"]
        
        # Ensure reasoning is not empty
        reasoning = dimension_data.get("reasoning", "")
        if not reasoning:
            reasoning = f"Analysis for {dimension_name} dimension"
        
        return DimensionScore(
            dimension_name=dimension_name,
            percentage=percentage,
            evidence=evidence,
            reasoning=reasoning,
            confidence_level=confidence_level
        )
    
    def _create_default_dimension_score(self, dimension_name: str) -> DimensionScore:
        """Create a default dimension score when analysis fails.
        
        Args:
            dimension_name: Name of the dimension
            
        Returns:
            Default DimensionScore object
        """
        return DimensionScore(
            dimension_name=dimension_name,
            percentage=0.0,
            evidence=["Insufficient data for analysis"],
            reasoning=f"No clear evidence found for {dimension_name} dimension",
            confidence_level=20  # Low confidence as numerical value
        )
    
    def _validate_confidence_level(self, confidence_level) -> int:
        """Return confidence level as integer, handling both string and numeric inputs.
        
        Args:
            confidence_level: Confidence level (string or numeric)
            
        Returns:
            Confidence level as integer (0-100)
        """
        # If it's already a number, return it as int
        if isinstance(confidence_level, (int, float)):
            return int(confidence_level)
        
        # If it's a string, convert common values to numbers
        if isinstance(confidence_level, str):
            confidence_str = confidence_level.lower().strip()
            if confidence_str in ["high", "높음"]:
                return 85
            elif confidence_str in ["medium", "중간"]:
                return 50
            elif confidence_str in ["low", "낮음"]:
                return 20
            else:
                # Try to parse as number
                try:
                    return int(float(confidence_str))
                except ValueError:
                    logger.warning(f"Unknown confidence level: {confidence_level}, defaulting to medium")
                    return 50
        
        # Default fallback
        return 50
    
    def _create_empty_portrait(self, user_id: str) -> DimensionPortrait:
        """Create an empty dimension portrait when no data is available.
        
        Args:
            user_id: Target user ID
            
        Returns:
            Empty DimensionPortrait
        """
        current_time = datetime.now()
        
        return DimensionPortrait(
            user_id=user_id,
            creativity=self._create_default_dimension_score("creativity"),
            aesthetic=self._create_default_dimension_score("aesthetic"),
            technology=self._create_default_dimension_score("technology"),
            literature=self._create_default_dimension_score("literature"),
            analysis_timestamp=current_time,
            total_summaries_analyzed=0,
            historical_trends=None
        )
    
    def _calculate_historical_trends(
        self,
        existing_portrait: DimensionPortrait
    ) -> List[Dict[str, Any]]:
        """Calculate historical trends from existing portrait.
        
        Args:
            existing_portrait: Previous dimension portrait
            
        Returns:
            List of historical trend data
        """
        if not existing_portrait:
            return []
        
        # Create trend entry from existing portrait
        trend_entry = {
            "timestamp": existing_portrait.analysis_timestamp.isoformat(),
            "creativity": existing_portrait.creativity.percentage,
            "aesthetic": existing_portrait.aesthetic.percentage,
            "technology": existing_portrait.technology.percentage,
            "literature": existing_portrait.literature.percentage,
            "total_summaries": existing_portrait.total_summaries_analyzed
        }
        
        # Combine with existing trends
        existing_trends = existing_portrait.historical_trends or []
        
        # Keep only recent trends (last 10 entries)
        all_trends = existing_trends + [trend_entry]
        return all_trends[-10:]