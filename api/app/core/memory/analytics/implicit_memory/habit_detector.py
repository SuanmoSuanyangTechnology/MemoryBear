"""Habit Detector for Implicit Memory System

This module implements the HabitDetector class that specializes in identifying
and ranking behavioral habits from user memory summaries. It provides advanced
habit analysis with confidence scoring, recency weighting, and current vs past
habit distinction.
"""

import logging
from datetime import datetime, timedelta
from typing import List, Optional

from app.core.memory.analytics.implicit_memory.analyzers.habit_analyzer import (
    HabitAnalyzer,
)
from app.core.memory.llm_tools.llm_client import LLMClientException
from app.schemas.implicit_memory_schema import (
    BehaviorHabit,
    ConfidenceLevel,
    FrequencyPattern,
    UserMemorySummary,
)
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class HabitDetector:
    """Detects and ranks behavioral habits from user memory summaries."""
    
    def __init__(
        self,
        db: Session,
        llm_model_id: Optional[str] = None
    ):
        """Initialize the habit detector.
        
        Args:
            db: Database session
            llm_model_id: Optional LLM model ID to use for analysis
        """
        self.db = db
        self.llm_model_id = llm_model_id
        self.habit_analyzer = HabitAnalyzer(db, llm_model_id)
    
    async def detect_habits(
        self,
        user_id: str,
        user_summaries: List[UserMemorySummary],
        existing_habits: Optional[List[BehaviorHabit]] = None
    ) -> List[BehaviorHabit]:
        """Detect behavioral habits from user summaries.
        
        Args:
            user_id: Target user ID
            user_summaries: List of user-specific memory summaries
            existing_habits: Optional existing habits for consolidation
            
        Returns:
            List of detected and ranked behavioral habits
            
        Raises:
            LLMClientException: If habit analysis fails
        """
        if not user_summaries:
            logger.warning(f"No summaries provided for user {user_id}")
            return existing_habits or []
        
        logger.info(f"Detecting habits for user {user_id} with {len(user_summaries)} summaries")
        
        try:
            # Use the habit analyzer to extract habits
            detected_habits = await self.habit_analyzer.analyze_habits(
                user_id=user_id,
                user_summaries=user_summaries,
                existing_habits=existing_habits
            )
            
            # Apply advanced ranking and filtering
            ranked_habits = self.rank_habits_by_confidence_and_recency(detected_habits)
            
            # Distinguish current vs past habits
            categorized_habits = self.distinguish_current_vs_past_habits(ranked_habits)
            
            logger.info(f"Detected {len(categorized_habits)} habits for user {user_id}")
            return categorized_habits
            
        except LLMClientException:
            logger.error(f"Habit detection failed for user {user_id}")
            raise
        except Exception as e:
            logger.error(f"Habit detection failed for user {user_id}: {e}")
            raise LLMClientException(f"Habit detection failed: {e}") from e
    
    def rank_habits_by_confidence_and_recency(
        self,
        habits: List[BehaviorHabit],
        confidence_weight: float = 0.6,
        recency_weight: float = 0.4
    ) -> List[BehaviorHabit]:
        """Rank habits by confidence level and recency.
        
        Args:
            habits: List of habits to rank
            confidence_weight: Weight for confidence score (0.0-1.0)
            recency_weight: Weight for recency score (0.0-1.0)
            
        Returns:
            List of habits ranked by combined score
        """
        if not habits:
            return []
        
        logger.info(f"Ranking {len(habits)} habits by confidence and recency")
        
        def calculate_ranking_score(habit: BehaviorHabit) -> float:
            """Calculate combined ranking score for a habit."""
            
            # Confidence score (0.0-1.0)
            confidence_scores = {
                ConfidenceLevel.HIGH: 1.0,
                ConfidenceLevel.MEDIUM: 0.6,
                ConfidenceLevel.LOW: 0.3
            }
            confidence_score = confidence_scores.get(habit.confidence_level, 0.3)
            
            # Recency score (0.0-1.0)
            current_time = datetime.now()
            days_since_last = (current_time - habit.last_observed).days
            
            # Exponential decay for recency (habits lose relevance over time)
            if days_since_last <= 7:
                recency_score = 1.0  # Very recent
            elif days_since_last <= 30:
                recency_score = 0.8  # Recent
            elif days_since_last <= 90:
                recency_score = 0.5  # Somewhat recent
            elif days_since_last <= 180:
                recency_score = 0.3  # Old
            else:
                recency_score = 0.1  # Very old
            
            # Frequency pattern bonus
            frequency_bonuses = {
                FrequencyPattern.DAILY: 0.2,
                FrequencyPattern.WEEKLY: 0.15,
                FrequencyPattern.MONTHLY: 0.1,
                FrequencyPattern.SEASONAL: 0.05,
                FrequencyPattern.OCCASIONAL: 0.0,
                FrequencyPattern.EVENT_TRIGGERED: 0.05
            }
            frequency_bonus = frequency_bonuses.get(habit.frequency_pattern, 0.0)
            
            # Evidence quality bonus
            evidence_bonus = min(len(habit.supporting_summaries) / 10.0, 0.1)  # Max 0.1 bonus
            
            # Current habit bonus
            current_bonus = 0.1 if habit.is_current else 0.0
            
            # Calculate final score
            base_score = (confidence_score * confidence_weight + 
                         recency_score * recency_weight)
            
            final_score = base_score + frequency_bonus + evidence_bonus + current_bonus
            
            return min(final_score, 1.0)  # Cap at 1.0
        
        # Sort habits by ranking score (descending)
        ranked_habits = sorted(habits, key=calculate_ranking_score, reverse=True)
        
        logger.info(f"Ranked habits with scores: {[calculate_ranking_score(h) for h in ranked_habits[:5]]}")
        
        return ranked_habits
    
    def distinguish_current_vs_past_habits(
        self,
        habits: List[BehaviorHabit],
        current_threshold_days: int = 30
    ) -> List[BehaviorHabit]:
        """Distinguish between current and past habits based on recency.
        
        Args:
            habits: List of habits to categorize
            current_threshold_days: Days threshold for considering a habit current
            
        Returns:
            List of habits with updated is_current status
        """
        if not habits:
            return []
        
        current_time = datetime.now()
        cutoff_date = current_time - timedelta(days=current_threshold_days)
        
        current_habits = []
        past_habits = []
        
        for habit in habits:
            # Update is_current status based on last observation
            if habit.last_observed >= cutoff_date:
                # Create updated habit with is_current = True
                updated_habit = BehaviorHabit(
                    habit_description=habit.habit_description,
                    frequency_pattern=habit.frequency_pattern,
                    time_context=habit.time_context,
                    confidence_level=habit.confidence_level,
                    supporting_summaries=habit.supporting_summaries,
                    specific_examples=habit.specific_examples,
                    first_observed=habit.first_observed,
                    last_observed=habit.last_observed,
                    is_current=True
                )
                current_habits.append(updated_habit)
            else:
                # Create updated habit with is_current = False
                updated_habit = BehaviorHabit(
                    habit_description=habit.habit_description,
                    frequency_pattern=habit.frequency_pattern,
                    time_context=habit.time_context,
                    confidence_level=habit.confidence_level,
                    supporting_summaries=habit.supporting_summaries,
                    specific_examples=habit.specific_examples,
                    first_observed=habit.first_observed,
                    last_observed=habit.last_observed,
                    is_current=False
                )
                past_habits.append(updated_habit)
        
        # Return current habits first, then past habits
        categorized_habits = current_habits + past_habits
        
        logger.info(f"Categorized habits: {len(current_habits)} current, {len(past_habits)} past")
        
        return categorized_habits