"""
Multimodal input processor for handling image and audio content.

This module provides utilities for detecting and processing multimodal inputs
(images and audio files) by converting them to text using appropriate models.
"""

import logging
from typing import List

from app.core.memory.agent.multimodal.speech_model import Vico_recognition
from app.core.memory.agent.utils.llm_tools import picture_model_requests

logger = logging.getLogger(__name__)


class MultimodalProcessor:
    """
    Processor for handling multimodal inputs (images and audio).
    
    This class detects image and audio file paths in input content and converts
    them to text using appropriate recognition models.
    """
    
    # Supported file extensions
    IMAGE_EXTENSIONS = ['.jpg', '.png']
    AUDIO_EXTENSIONS = [
        'aac', 'amr', 'avi', 'flac', 'flv', 'm4a', 'mkv', 'mov',
        'mp3', 'mp4', 'mpeg', 'ogg', 'opus', 'wav', 'webm', 'wma', 'wmv'
    ]
    
    def __init__(self):
        """Initialize the multimodal processor."""
        pass
    
    def is_image(self, content: str) -> bool:
        """
        Check if content is an image file path.
        
        Args:
            content: Input string to check
            
        Returns:
            True if content ends with a supported image extension
            
        Examples:
            >>> processor = MultimodalProcessor()
            >>> processor.is_image("photo.jpg")
            True
            >>> processor.is_image("document.pdf")
            False
        """
        if not isinstance(content, str):
            return False
        
        content_lower = content.lower()
        return any(content_lower.endswith(ext) for ext in self.IMAGE_EXTENSIONS)
    
    def is_audio(self, content: str) -> bool:
        """
        Check if content is an audio file path.
        
        Args:
            content: Input string to check
            
        Returns:
            True if content ends with a supported audio extension
            
        Examples:
            >>> processor = MultimodalProcessor()
            >>> processor.is_audio("recording.mp3")
            True
            >>> processor.is_audio("video.mp4")
            True
            >>> processor.is_audio("document.txt")
            False
        """
        if not isinstance(content, str):
            return False
        
        content_lower = content.lower()
        return any(content_lower.endswith(f'.{ext}') for ext in self.AUDIO_EXTENSIONS)
    
    async def process_input(self, content: str) -> str:
        """
        Process input content, converting images/audio to text if needed.
        
        This method detects if the input is an image or audio file and converts
        it to text using the appropriate recognition model. If processing fails
        or the content is not multimodal, it returns the original content.
        
        Args:
            content: Input string (may be file path or regular text)
            
        Returns:
            Text content (original or converted from image/audio)
            
        Examples:
            >>> processor = MultimodalProcessor()
            >>> await processor.process_input("photo.jpg")
            "Recognized text from image..."
            
            >>> await processor.process_input("Hello world")
            "Hello world"
        """
        if not isinstance(content, str):
            logger.warning(f"[MultimodalProcessor] Content is not a string: {type(content)}")
            return str(content)
        
        try:
            # Check for image input
            if self.is_image(content):
                logger.info(f"[MultimodalProcessor] Detected image input: {content}")
                result = await picture_model_requests(content)
                logger.info(f"[MultimodalProcessor] Image recognition result: {result[:100]}...")
                return result
            
            # Check for audio input
            if self.is_audio(content):
                logger.info(f"[MultimodalProcessor] Detected audio input: {content}")
                result = await Vico_recognition([content]).run()
                logger.info(f"[MultimodalProcessor] Audio recognition result: {result[:100]}...")
                return result
        
        except Exception as e:
            logger.error(f"[MultimodalProcessor] Error processing multimodal input: {e}", exc_info=True)
            logger.info("[MultimodalProcessor] Falling back to original content")
            return content
        
        # Return original content if not multimodal
        return content
