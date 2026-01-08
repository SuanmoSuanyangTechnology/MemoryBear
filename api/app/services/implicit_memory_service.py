"""
Implicit Memory Service

Main service orchestrating all implicit memory operations. This service coordinates
profile building, data extraction, and provides high-level methods for analyzing
user profiles from memory summaries.
"""

import logging
from datetime import datetime
from typing import List, Optional

from app.core.memory.analytics.implicit_memory.analyzers.dimension_analyzer import (
    DimensionAnalyzer,
)
from app.core.memory.analytics.implicit_memory.analyzers.interest_analyzer import (
    InterestAnalyzer,
)
from app.core.memory.analytics.implicit_memory.analyzers.preference_analyzer import (
    PreferenceAnalyzer,
)
from app.core.memory.analytics.implicit_memory.data_source import MemoryDataSource
from app.core.memory.analytics.implicit_memory.habit_detector import HabitDetector
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.schemas.implicit_memory_schema import (
    BehaviorHabit,
    DateRange,
    DimensionPortrait,
    FrequencyPattern,
    InterestAreaDistribution,
    PreferenceTag,
    TimeRange,
    UserMemorySummary,
)
from app.schemas.memory_config_schema import MemoryConfig
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class ImplicitMemoryService:
    """Main service for implicit memory operations."""
    
    def __init__(
        self,
        db: Session,
        end_user_id: str
    ):
        """Initialize the implicit memory service.
        
        Args:
            db: Database session
            end_user_id: End user ID to get connected memory configuration
        """
        self.db = db
        self.end_user_id = end_user_id
        
        # Get connected memory configuration for the user
        self.memory_config = self._get_user_memory_config()
        
        # Extract LLM model ID from memory config
        llm_model_id = str(self.memory_config.llm_model_id) if self.memory_config.llm_model_id else None
        
        # Initialize Neo4j connector
        self.neo4j_connector = Neo4jConnector()
        
        # Initialize core components with LLM model ID
        self.data_source = MemoryDataSource(db, self.neo4j_connector)
        self.preference_analyzer = PreferenceAnalyzer(db, llm_model_id)
        self.dimension_analyzer = DimensionAnalyzer(db, llm_model_id)
        self.interest_analyzer = InterestAnalyzer(db, llm_model_id)
        self.habit_detector = HabitDetector(db, llm_model_id)
        
        logger.info(f"ImplicitMemoryService initialized for end_user: {end_user_id}")
    
    def _get_user_memory_config(self) -> MemoryConfig:
        """Get memory configuration for the connected end user.
        
        Returns:
            MemoryConfig: User's connected memory configuration
            
        Raises:
            ValueError: If no memory configuration found for user
        """
        try:
            from app.services.memory_agent_service import get_end_user_connected_config
            from app.services.memory_config_service import MemoryConfigService
            
            # Get user's connected config
            connected_config = get_end_user_connected_config(self.end_user_id, self.db)
            config_id = connected_config.get("memory_config_id")
            
            if config_id is None:
                raise ValueError(f"No memory configuration found for end_user: {self.end_user_id}")
            
            # Load the memory configuration
            config_service = MemoryConfigService(self.db)
            memory_config = config_service.load_memory_config(config_id)
            
            logger.info(f"Loaded memory config {config_id} for end_user: {self.end_user_id}")
            return memory_config
            
        except Exception as e:
            logger.error(f"Failed to get memory config for end_user {self.end_user_id}: {e}")
            raise ValueError(f"Unable to get memory configuration for end_user {self.end_user_id}: {e}")
    
    async def extract_user_summaries(
        self,
        user_id: str,
        time_range: Optional[TimeRange] = None,
        limit: Optional[int] = None
    ) -> List[UserMemorySummary]:
        """Extract user-specific memory summaries.
        
        Args:
            user_id: Target user ID
            time_range: Optional time range to filter summaries
            limit: Optional limit on number of summaries
            
        Returns:
            List of user-specific memory summaries
        """
        logger.info(f"Extracting user summaries for user {user_id}")
        
        try:
            summaries = await self.data_source.get_user_summaries(
                user_id=user_id,
                time_range=time_range,
                limit=limit or 1000
            )
            
            logger.info(f"Extracted {len(summaries)} summaries for user {user_id}")
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
                    end_date=date_range.end_date or datetime.now()
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
    