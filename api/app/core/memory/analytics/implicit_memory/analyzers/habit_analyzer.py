"""Habit Analyzer for Implicit Memory System

This module implements LLM-based behavioral habit analysis from user memory summaries.
It identifies recurring behavioral patterns, temporal patterns, and consolidates
similar habits with confidence scoring.
"""

import logging
from datetime import datetime
from typing import List, Optional

from app.core.memory.analytics.implicit_memory.llm_client import ImplicitMemoryLLMClient
from app.core.memory.llm_tools.llm_client import LLMClientException
from app.schemas.implicit_memory_schema import (
    BehaviorHabit,
    FrequencyPattern,
    UserMemorySummary,
)
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class HabitData(BaseModel):
    """Individual habit analysis data."""
    habit_description: str
    frequency_pattern: str
    time_context: str
    confidence_level: int = 50  # Default to medium confidence
    supporting_summaries: List[str] = Field(default_factory=list)
    specific_examples: List[str] = Field(default_factory=list)
    is_current: bool = True


class HabitAnalysisResponse(BaseModel):
    """Response model for habit analysis."""
    habits: List[HabitData] = Field(default_factory=list)


class HabitAnalyzer:
    """Analyzes user memory summaries to extract behavioral habits."""
    
    def __init__(self, db: Session, llm_model_id: Optional[str] = None):
        """Initialize the habit analyzer.
        
        Args:
            db: Database session
            llm_model_id: Optional LLM model ID to use for analysis
        """
        self.db = db
        self.llm_model_id = llm_model_id
        self._llm_client = ImplicitMemoryLLMClient(db, llm_model_id)
    
    async def analyze_habits(
        self,
        user_id: str,
        user_summaries: List[UserMemorySummary],
        existing_habits: Optional[List[BehaviorHabit]] = None
    ) -> List[BehaviorHabit]:
        """Analyze user summaries to extract behavioral habits.
        
        Args:
            user_id: Target user ID
            user_summaries: List of user-specific memory summaries
            existing_habits: Optional existing habits for consolidation
            
        Returns:
            List of extracted behavioral habits
            
        Raises:
            LLMClientException: If LLM analysis fails
        """
        if not user_summaries:
            logger.warning(f"No summaries provided for user {user_id}")
            return existing_habits or []
        
        try:
            logger.info(f"Analyzing habits for user {user_id} with {len(user_summaries)} summaries")
            
            # Use the LLM client wrapper for analysis
            response = await self._llm_client.analyze_habits(
                user_summaries=user_summaries,
                user_id=user_id,
                model_id=self.llm_model_id
            )
            
            # Convert to BehaviorHabit objects
            behavior_habits = []
            
            for habit_data in response.get("habits", []):
                try:
                    # Handle habit_data as dictionary
                    supporting_summaries = habit_data.get("supporting_summaries", [])
                    specific_examples = habit_data.get("specific_examples", [])
                    
                    # Determine observation dates from summaries
                    first_observed, last_observed = self._determine_observation_dates(
                        user_summaries, supporting_summaries
                    )
                    
                    behavior_habit = BehaviorHabit(
                        habit_description=habit_data.get("habit_description", ""),
                        frequency_pattern=self._validate_frequency_pattern(habit_data.get("frequency_pattern", "occasional")),
                        time_context=habit_data.get("time_context", ""),
                        confidence_level=self._validate_confidence_level(habit_data.get("confidence_level", 50)),
                        specific_examples=specific_examples,
                        first_observed=first_observed,
                        last_observed=last_observed,
                        is_current=habit_data.get("is_current", True)
                    )
                    
                    # Validate habit
                    if self._is_valid_habit(behavior_habit):
                        behavior_habits.append(behavior_habit)
                    else:
                        logger.warning(f"Invalid habit skipped: {behavior_habit.habit_description}")
                        
                except Exception as e:
                    logger.error(f"Error creating behavior habit: {e}")
                    continue
            
            # Consolidate with existing habits if provided
            if existing_habits:
                behavior_habits = self._consolidate_habits(
                    new_habits=behavior_habits,
                    existing_habits=existing_habits
                )
            
            # Sort habits by confidence and recency
            behavior_habits = self._sort_habits_by_priority(behavior_habits)
            
            logger.info(f"Extracted {len(behavior_habits)} habits for user {user_id}")
            return behavior_habits
            
        except LLMClientException:
            raise
        except Exception as e:
            logger.error(f"Habit analysis failed for user {user_id}: {e}")
            raise LLMClientException(f"Habit analysis failed: {e}") from e
    
    def _validate_frequency_pattern(self, frequency_str: str) -> FrequencyPattern:
        """Validate and convert frequency pattern string.
        
        Args:
            frequency_str: Frequency pattern as string
            
        Returns:
            FrequencyPattern enum value
        """
        frequency_str = frequency_str.lower().strip()
        
        frequency_mapping = {
            "daily": FrequencyPattern.DAILY,
            "weekly": FrequencyPattern.WEEKLY,
            "monthly": FrequencyPattern.MONTHLY,
            "seasonal": FrequencyPattern.SEASONAL,
            "occasional": FrequencyPattern.OCCASIONAL,
            "event_triggered": FrequencyPattern.EVENT_TRIGGERED,
            "event-triggered": FrequencyPattern.EVENT_TRIGGERED,
        }
        
        return frequency_mapping.get(frequency_str, FrequencyPattern.OCCASIONAL)
    
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
    
    def _determine_observation_dates(
        self,
        user_summaries: List[UserMemorySummary],
        supporting_summary_ids: List[str]
    ) -> tuple[datetime, datetime]:
        """Determine first and last observation dates for a habit.
        
        Args:
            user_summaries: List of user summaries
            supporting_summary_ids: IDs of summaries supporting the habit
            
        Returns:
            Tuple of (first_observed, last_observed) dates
        """
        from datetime import timezone
        
        # Find summaries that support this habit
        supporting_summaries = [
            summary for summary in user_summaries
            if summary.summary_id in supporting_summary_ids
        ]
        
        if not supporting_summaries:
            # Use all summaries if no specific supporting summaries found
            supporting_summaries = user_summaries
        
        if not supporting_summaries:
            current_time = datetime.now(timezone.utc).replace(tzinfo=None)
            return current_time, current_time
        
        # Get date range from supporting summaries - normalize to naive datetimes
        timestamps = []
        for summary in supporting_summaries:
            ts = summary.timestamp
            # Convert to naive datetime if it's timezone-aware
            if ts.tzinfo is not None:
                ts = ts.replace(tzinfo=None)
            timestamps.append(ts)
        
        first_observed = min(timestamps)
        last_observed = max(timestamps)
        
        return first_observed, last_observed
    
    def _is_valid_habit(self, habit: BehaviorHabit) -> bool:
        """Validate a behavioral habit.
        
        Args:
            habit: Behavioral habit to validate
            
        Returns:
            True if valid, False otherwise
        """
        try:
            # Check required fields
            if not habit.habit_description or not habit.habit_description.strip():
                return False
            
            # Check time context
            if not habit.time_context or not habit.time_context.strip():
                return False
            
            # Check supporting summaries
            if not habit.specific_examples or len(habit.specific_examples) == 0:
                return False
            
            # Check specific examples
            if not habit.specific_examples or len(habit.specific_examples) == 0:
                return False
            
            # Check observation dates
            if habit.first_observed > habit.last_observed:
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"Error validating habit: {e}")
            return False
    
    def _consolidate_habits(
        self,
        new_habits: List[BehaviorHabit],
        existing_habits: List[BehaviorHabit],
        similarity_threshold: float = 0.7
    ) -> List[BehaviorHabit]:
        """Consolidate new habits with existing ones.
        
        Args:
            new_habits: Newly extracted habits
            existing_habits: Existing habits
            similarity_threshold: Threshold for considering habits similar
            
        Returns:
            Consolidated list of habits
        """
        consolidated = existing_habits.copy()
        current_time = datetime.now()
        
        for new_habit in new_habits:
            # Find similar existing habit
            similar_habit = self._find_similar_habit(
                new_habit, existing_habits, similarity_threshold
            )
            
            if similar_habit:
                # Update existing habit
                updated_habit = self._merge_habits(similar_habit, new_habit, current_time)
                # Replace in consolidated list
                for i, habit in enumerate(consolidated):
                    if habit.habit_description == similar_habit.habit_description:
                        consolidated[i] = updated_habit
                        break
            else:
                # Add as new habit
                consolidated.append(new_habit)
        
        return consolidated
    
    def _find_similar_habit(
        self,
        target_habit: BehaviorHabit,
        existing_habits: List[BehaviorHabit],
        threshold: float
    ) -> Optional[BehaviorHabit]:
        """Find similar habit in existing list.
        
        Args:
            target_habit: Habit to find similarity for
            existing_habits: List of existing habits
            threshold: Similarity threshold
            
        Returns:
            Similar habit if found, None otherwise
        """
        target_desc = target_habit.habit_description.lower().strip()
        
        for existing_habit in existing_habits:
            existing_desc = existing_habit.habit_description.lower().strip()
            
            # Check description similarity
            desc_similarity = self._calculate_text_similarity(target_desc, existing_desc)
            
            # Check frequency pattern match
            frequency_match = (target_habit.frequency_pattern == existing_habit.frequency_pattern)
            
            # Check time context similarity
            time_similarity = self._calculate_text_similarity(
                target_habit.time_context.lower(),
                existing_habit.time_context.lower()
            )
            
            # Combined similarity score
            combined_similarity = (desc_similarity * 0.6 + time_similarity * 0.4)
            if frequency_match:
                combined_similarity += 0.1  # Bonus for frequency match
            
            if combined_similarity >= threshold:
                return existing_habit
        
        return None
    
    def _calculate_text_similarity(self, text1: str, text2: str) -> float:
        """Calculate simple text similarity based on common words.
        
        Args:
            text1: First text
            text2: Second text
            
        Returns:
            Similarity score between 0.0 and 1.0
        """
        if not text1 or not text2:
            return 0.0
        
        # Simple word-based similarity
        words1 = set(text1.lower().split())
        words2 = set(text2.lower().split())
        
        if not words1 or not words2:
            return 0.0
        
        intersection = words1.intersection(words2)
        union = words1.union(words2)
        
        return len(intersection) / len(union) if union else 0.0
    
    def _merge_habits(
        self,
        existing_habit: BehaviorHabit,
        new_habit: BehaviorHabit,
        current_time: datetime
    ) -> BehaviorHabit:
        """Merge two similar habits.
        
        Args:
            existing_habit: Existing habit
            new_habit: New habit to merge
            current_time: Current timestamp
            
        Returns:
            Merged behavioral habit
        """
        # Combine supporting summaries (using specific_examples instead)
        combined_examples = list(set(
            existing_habit.specific_examples + new_habit.specific_examples
        ))
        
        # Combine specific examples
        combined_examples = list(set(
            existing_habit.specific_examples + new_habit.specific_examples
        ))
        
        # Update confidence level (take higher confidence)
        new_confidence = max(existing_habit.confidence_level, new_habit.confidence_level)
        
        # Update observation dates
        first_observed = min(existing_habit.first_observed, new_habit.first_observed)
        last_observed = max(existing_habit.last_observed, new_habit.last_observed)
        
        # Determine if habit is current (observed within last 30 days)
        is_current = (current_time - last_observed).days <= 30
        
        # Combine time context
        combined_time_context = existing_habit.time_context
        if new_habit.time_context and new_habit.time_context not in combined_time_context:
            combined_time_context += f"; {new_habit.time_context}"
        
        return BehaviorHabit(
            habit_description=existing_habit.habit_description,  # Keep original description
            frequency_pattern=existing_habit.frequency_pattern,  # Keep original frequency
            time_context=combined_time_context,
            confidence_level=new_confidence,
            specific_examples=combined_examples,
            first_observed=first_observed,
            last_observed=last_observed,
            is_current=is_current
        )
    
    def _sort_habits_by_priority(self, habits: List[BehaviorHabit]) -> List[BehaviorHabit]:
        """Sort habits by confidence level and recency.
        
        Args:
            habits: List of habits to sort
            
        Returns:
            Sorted list of habits
        """
        def priority_score(habit: BehaviorHabit) -> tuple:
            # Confidence level score (0-100 scale)
            confidence_score = habit.confidence_level
            
            # Recency score (more recent = higher score)
            days_since_last = (datetime.now() - habit.last_observed).days
            recency_score = max(0, 365 - days_since_last)  # Max 365 days
            
            # Current habit bonus
            current_bonus = 100 if habit.is_current else 0
            
            return (confidence_score, recency_score + current_bonus, habit.last_observed)
        
        return sorted(habits, key=priority_score, reverse=True)