"""Preference Analyzer for Implicit Memory System

This module implements LLM-based preference extraction from user memory summaries.
It identifies implicit preferences, consolidates similar preferences, and calculates
confidence scores based on evidence strength.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.core.memory.analytics.implicit_memory.llm_client import ImplicitMemoryLLMClient
from app.core.memory.llm_tools.llm_client import LLMClientException
from app.schemas.implicit_memory_schema import (
    PreferenceTag,
    UserMemorySummary,
)
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class PreferenceAnalysisResponse(BaseModel):
    """Response model for preference analysis."""
    preferences: List[Dict[str, Any]] = Field(default_factory=list)


class PreferenceAnalyzer:
    """Analyzes user memory summaries to extract implicit preferences."""
    
    def __init__(self, db: Session, llm_model_id: Optional[str] = None):
        """Initialize the preference analyzer.
        
        Args:
            db: Database session
            llm_model_id: Optional LLM model ID to use for analysis
        """
        self.db = db
        self.llm_model_id = llm_model_id
        self._llm_client = ImplicitMemoryLLMClient(db, llm_model_id)
    
    async def analyze_preferences(
        self,
        user_id: str,
        user_summaries: List[UserMemorySummary],
        existing_preferences: Optional[List[PreferenceTag]] = None
    ) -> List[PreferenceTag]:
        """Analyze user summaries to extract preferences.
        
        Args:
            user_id: Target user ID
            user_summaries: List of user-specific memory summaries
            existing_preferences: Optional existing preferences for consolidation
            
        Returns:
            List of extracted preference tags
            
        Raises:
            LLMClientException: If LLM analysis fails
        """
        if not user_summaries:
            logger.warning(f"No summaries provided for user {user_id}")
            return []
        
        try:
            logger.info(f"Analyzing preferences for user {user_id} with {len(user_summaries)} summaries")
            
            # Use the LLM client wrapper for analysis
            response = await self._llm_client.analyze_preferences(
                user_summaries=user_summaries,
                user_id=user_id,
                model_id=self.llm_model_id
            )
            
            # Convert to PreferenceTag objects
            preference_tags = []
            current_time = datetime.now()
            
            for pref_data in response.get("preferences", []):
                try:
                    # Extract conversation references from summaries
                    conversation_refs = [s.summary_id for s in user_summaries]
                    
                    preference_tag = PreferenceTag(
                        tag_name=pref_data.get("tag_name", ""),
                        confidence_score=float(pref_data.get("confidence_score", 0.0)),
                        supporting_evidence=pref_data.get("supporting_evidence", []),
                        context_details=pref_data.get("context_details", ""),
                        category=pref_data.get("category"),
                        conversation_references=conversation_refs,
                        created_at=current_time,
                        updated_at=current_time
                    )
                    
                    # Validate preference tag
                    if self._is_valid_preference(preference_tag):
                        preference_tags.append(preference_tag)
                    else:
                        logger.warning(f"Invalid preference tag skipped: {preference_tag.tag_name}")
                        
                except Exception as e:
                    logger.error(f"Error creating preference tag: {e}")
                    continue
            
            # Consolidate with existing preferences if provided
            if existing_preferences:
                preference_tags = self._consolidate_preferences(
                    new_preferences=preference_tags,
                    existing_preferences=existing_preferences
                )
            
            logger.info(f"Extracted {len(preference_tags)} preferences for user {user_id}")
            return preference_tags
            
        except LLMClientException:
            raise
        except Exception as e:
            logger.error(f"Preference analysis failed for user {user_id}: {e}")
            raise LLMClientException(f"Preference analysis failed: {e}") from e
    
    def _is_valid_preference(self, preference: PreferenceTag) -> bool:
        """Validate a preference tag.
        
        Args:
            preference: Preference tag to validate
            
        Returns:
            True if valid, False otherwise
        """
        try:
            # Check required fields
            if not preference.tag_name or not preference.tag_name.strip():
                return False
            
            # Check confidence score range
            if not (0.0 <= preference.confidence_score <= 1.0):
                return False
            
            # Check supporting evidence
            if not preference.supporting_evidence or len(preference.supporting_evidence) == 0:
                return False
            
            # Check context details
            if not preference.context_details or not preference.context_details.strip():
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"Error validating preference: {e}")
            return False
    
    def _consolidate_preferences(
        self,
        new_preferences: List[PreferenceTag],
        existing_preferences: List[PreferenceTag],
        similarity_threshold: float = 0.8
    ) -> List[PreferenceTag]:
        """Consolidate new preferences with existing ones.
        
        Args:
            new_preferences: Newly extracted preferences
            existing_preferences: Existing preferences
            similarity_threshold: Threshold for considering preferences similar
            
        Returns:
            Consolidated list of preferences
        """
        consolidated = existing_preferences.copy()
        current_time = datetime.now()
        
        for new_pref in new_preferences:
            # Find similar existing preference
            similar_pref = self._find_similar_preference(
                new_pref, existing_preferences, similarity_threshold
            )
            
            if similar_pref:
                # Update existing preference
                updated_pref = self._merge_preferences(similar_pref, new_pref, current_time)
                # Replace in consolidated list
                for i, pref in enumerate(consolidated):
                    if pref.tag_name == similar_pref.tag_name:
                        consolidated[i] = updated_pref
                        break
            else:
                # Add as new preference
                consolidated.append(new_pref)
        
        return consolidated
    
    def _find_similar_preference(
        self,
        target_preference: PreferenceTag,
        existing_preferences: List[PreferenceTag],
        threshold: float
    ) -> Optional[PreferenceTag]:
        """Find similar preference in existing list.
        
        Args:
            target_preference: Preference to find similarity for
            existing_preferences: List of existing preferences
            threshold: Similarity threshold
            
        Returns:
            Similar preference if found, None otherwise
        """
        target_name = target_preference.tag_name.lower().strip()
        
        for existing_pref in existing_preferences:
            existing_name = existing_pref.tag_name.lower().strip()
            
            # Simple similarity check based on common words
            similarity = self._calculate_text_similarity(target_name, existing_name)
            
            if similarity >= threshold:
                return existing_pref
        
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
    
    def _merge_preferences(
        self,
        existing_pref: PreferenceTag,
        new_pref: PreferenceTag,
        current_time: datetime
    ) -> PreferenceTag:
        """Merge two similar preferences.
        
        Args:
            existing_pref: Existing preference
            new_pref: New preference to merge
            current_time: Current timestamp
            
        Returns:
            Merged preference tag
        """
        # Combine supporting evidence
        combined_evidence = list(set(
            existing_pref.supporting_evidence + new_pref.supporting_evidence
        ))
        
        # Combine conversation references
        combined_refs = list(set(
            existing_pref.conversation_references + new_pref.conversation_references
        ))
        
        # Calculate new confidence score (weighted average)
        evidence_weight = len(new_pref.supporting_evidence)
        total_weight = len(existing_pref.supporting_evidence) + evidence_weight
        
        if total_weight > 0:
            new_confidence = (
                (existing_pref.confidence_score * len(existing_pref.supporting_evidence) +
                 new_pref.confidence_score * evidence_weight) / total_weight
            )
        else:
            new_confidence = max(existing_pref.confidence_score, new_pref.confidence_score)
        
        # Ensure confidence doesn't exceed 1.0
        new_confidence = min(new_confidence, 1.0)
        
        # Combine context details
        combined_context = existing_pref.context_details
        if new_pref.context_details and new_pref.context_details not in combined_context:
            combined_context += f"; {new_pref.context_details}"
        
        return PreferenceTag(
            tag_name=existing_pref.tag_name,  # Keep original name
            confidence_score=new_confidence,
            supporting_evidence=combined_evidence,
            context_details=combined_context,
            category=existing_pref.category or new_pref.category,
            conversation_references=combined_refs,
            created_at=existing_pref.created_at,
            updated_at=current_time
        )