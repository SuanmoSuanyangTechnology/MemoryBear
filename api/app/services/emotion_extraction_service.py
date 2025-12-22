"""Emotion extraction service for analyzing emotions from statements.

This service extracts emotion information from user statements using LLM,
including emotion type, intensity, keywords, subject classification, and target.

Classes:
    EmotionExtractionService: Service for extracting emotions from statements
"""

import logging
from typing import Optional
from app.core.memory.models.emotion_models import EmotionExtraction
from app.models.data_config_model import DataConfig
from app.core.memory.utils.llm.llm_utils import get_llm_client
from app.core.memory.llm_tools.llm_client import LLMClientException

logger = logging.getLogger(__name__)


class EmotionExtractionService:
    """Service for extracting emotion information from statements.
    
    This service uses LLM to analyze statements and extract structured emotion
    information including type, intensity, keywords, subject, and target.
    It respects configuration settings for enabling/disabling extraction and
    filtering by intensity threshold.
    
    Attributes:
        llm_client: LLM client for making structured output calls
    """
    
    def __init__(self, llm_id: Optional[str] = None):
        """Initialize the emotion extraction service.
        
        Args:
            llm_id: Optional LLM model ID. If None, uses default from config.
        """
        self.llm_client = None
        self.llm_id = llm_id
        logger.info(f"Initialized EmotionExtractionService with llm_id={llm_id}")
    
    def _get_llm_client(self, model_id: Optional[str] = None):
        """Get or create LLM client instance.
        
        Args:
            model_id: Optional model ID to use. If None, uses instance llm_id.
            
        Returns:
            LLM client instance
        """
        if self.llm_client is None or model_id:
            effective_model_id = model_id or self.llm_id
            self.llm_client = get_llm_client(effective_model_id)
        return self.llm_client
    
    async def extract_emotion(
        self,
        statement: str,
        config: DataConfig
    ) -> Optional[EmotionExtraction]:
        """Extract emotion information from a statement.
        
        This method checks if emotion extraction is enabled in the config,
        builds an appropriate prompt, calls the LLM for structured output,
        and applies intensity threshold filtering.
        
        Args:
            statement: The statement text to analyze
            config: Data configuration object containing emotion settings
            
        Returns:
            EmotionExtraction object if extraction succeeds and passes threshold,
            None if extraction is disabled, fails, or doesn't meet threshold
            
        Raises:
            No exceptions are raised - failures are logged and return None
        """
        # Check if emotion extraction is enabled
        if not config.emotion_enabled:
            logger.debug("Emotion extraction is disabled in config")
            return None
        
        # Validate statement
        if not statement or not statement.strip():
            logger.warning("Empty statement provided for emotion extraction")
            return None
        
        try:
            # Build the emotion extraction prompt
            prompt = await self._build_emotion_prompt(
                statement=statement,
                extract_keywords=config.emotion_extract_keywords,
                enable_subject=config.emotion_enable_subject
            )
            
            # Call LLM for structured output
            emotion = await self._call_llm_structured(
                prompt=prompt,
                model_id=config.emotion_model_id
            )
            
            # Apply intensity threshold filtering
            if emotion.emotion_intensity < config.emotion_min_intensity:
                logger.debug(
                    f"Emotion intensity {emotion.emotion_intensity} below threshold "
                    f"{config.emotion_min_intensity}, skipping storage"
                )
                return None
            
            logger.info(
                f"Successfully extracted emotion: type={emotion.emotion_type}, "
                f"intensity={emotion.emotion_intensity}, subject={emotion.emotion_subject}"
            )
            
            return emotion
            
        except Exception as e:
            logger.error(
                f"Emotion extraction failed for statement: {statement[:50]}..., "
                f"error: {str(e)}",
                exc_info=True
            )
            return None
   
    async def _build_emotion_prompt(
        self,
        statement: str,
        extract_keywords: bool,
        enable_subject: bool
    ) -> str:
        """Build the emotion extraction prompt based on configuration.
        
        This method constructs a detailed prompt for the LLM that includes
        instructions for emotion type classification, intensity assessment,
        and optionally keyword extraction and subject classification.
        
        Args:
            statement: The statement to analyze
            extract_keywords: Whether to extract emotion keywords
            enable_subject: Whether to enable subject classification
            
        Returns:
            Formatted prompt string for LLM
        """
        from app.core.memory.utils.prompt.prompt_utils import render_emotion_extraction_prompt
        
        prompt = await render_emotion_extraction_prompt(
            statement=statement,
            extract_keywords=extract_keywords,
            enable_subject=enable_subject
        )
        
        return prompt
    
    async def _call_llm_structured(
        self,
        prompt: str,
        model_id: Optional[str] = None
    ) -> EmotionExtraction:
        """Call LLM for structured emotion extraction output.
        
        This method uses the LLM client's response_structured method to get
        a validated EmotionExtraction object from the LLM.
        
        Args:
            prompt: The formatted prompt for emotion extraction
            model_id: Optional model ID to use for this call
            
        Returns:
            EmotionExtraction object with validated emotion data
            
        Raises:
            LLMClientException: If LLM call fails or times out
            ValidationError: If LLM response doesn't match expected schema
        """
        try:
            # Get LLM client
            llm_client = self._get_llm_client(model_id)
            
            # Prepare messages
            messages = [
                {"role": "user", "content": prompt}
            ]
            
            # Call LLM with structured output
            emotion = await llm_client.response_structured(
                messages=messages,
                response_model=EmotionExtraction,
                temperature=0.3,
                max_tokens=500
            )
            
            return emotion
            
        except LLMClientException as e:
            logger.error(f"LLM call failed: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error in LLM structured call: {str(e)}")
            raise LLMClientException(f"Emotion extraction LLM call failed: {str(e)}")
