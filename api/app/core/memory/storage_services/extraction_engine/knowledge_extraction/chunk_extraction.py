import os
from typing import Optional, List, Any
from enum import Enum
from pathlib import Path

from app.core.logging_config import get_memory_logger
from app.core.memory.models.message_models import DialogData, Chunk
from app.core.memory.models.config_models import ChunkerConfig
from app.core.memory.llm_tools.chunker_client import ChunkerClient
from app.core.memory.utils.config.config_utils import get_chunker_config

logger = get_memory_logger(__name__)


class ChunkerStrategy(Enum):
    """Supported chunking strategies."""
    RECURSIVE = "RecursiveChunker"
    SEMANTIC = "SemanticChunker"
    LATE = "LateChunker"
    NEURAL = "NeuralChunker"
    LLM = "LLMChunker"
    
    @classmethod
    def get_valid_strategies(cls) -> List[str]:
        """Get list of valid strategy names."""
        return [strategy.value for strategy in cls]


class DialogueChunker:
    """A class that processes dialogues and fills them with chunks based on a specified strategy.

    This class encapsulates the chunking process, allowing for easy configuration and application
    of different chunking strategies to dialogue data.
    """

    def __init__(self, chunker_strategy: str = "RecursiveChunker", llm_client: Optional[Any] = None):
        """Initialize the DialogueChunker with a specific chunking strategy.

        Args:
            chunker_strategy: The chunking strategy to use (default: RecursiveChunker)
                             Options: SemanticChunker, RecursiveChunker, LateChunker, NeuralChunker, LLMChunker
            llm_client: LLM client instance (required for LLMChunker strategy)
            
        Raises:
            ValueError: If chunker_strategy is invalid or required parameters are missing
        """
        # Validate strategy
        valid_strategies = ChunkerStrategy.get_valid_strategies()
        if chunker_strategy not in valid_strategies:
            raise ValueError(
                f"Invalid chunker_strategy: '{chunker_strategy}'. "
                f"Must be one of {valid_strategies}"
            )
        
        self.chunker_strategy = chunker_strategy
        logger.info(f"Initializing DialogueChunker with strategy: {chunker_strategy}")
        
        try:
            # Load and validate configuration
            chunker_config_dict = get_chunker_config(chunker_strategy)
            if not chunker_config_dict:
                raise ValueError(f"Failed to load configuration for strategy: {chunker_strategy}")
            
            self.chunker_config = ChunkerConfig.model_validate(chunker_config_dict)
            
            # Initialize chunker client
            if self.chunker_config.chunker_strategy == "LLMChunker":
                if not llm_client:
                    raise ValueError("llm_client is required for LLMChunker strategy")
                self.chunker_client = ChunkerClient(self.chunker_config, llm_client)
            else:
                self.chunker_client = ChunkerClient(self.chunker_config)
            
            logger.info(f"DialogueChunker initialized successfully with strategy: {chunker_strategy}")
            
        except Exception as e:
            logger.error(f"Failed to initialize DialogueChunker: {e}", exc_info=True)
            raise

    async def process_dialogue(self, dialogue: DialogData) -> List[Chunk]:
        """Process a dialogue by generating chunks and adding them to the DialogData object.

        Args:
            dialogue: The DialogData object to process

        Returns:
            A list of Chunk objects

        Raises:
            ValueError: If dialogue is invalid or chunking fails
            Exception: If chunking process encounters an error
        """
        # Validate input
        if not dialogue:
            raise ValueError("dialogue cannot be None")
        
        if not dialogue.context or not dialogue.context.msgs:
            raise ValueError(
                f"Dialogue {dialogue.ref_id} has no messages to chunk. "
                f"Context: {dialogue.context is not None}, "
                f"Messages: {len(dialogue.context.msgs) if dialogue.context else 0}"
            )
        
        logger.info(
            f"Processing dialogue {dialogue.ref_id} with {len(dialogue.context.msgs)} messages "
            f"using strategy: {self.chunker_strategy}"
        )
        
        try:
            # Generate chunks
            result_dialogue = await self.chunker_client.generate_chunks(dialogue)
            chunks = result_dialogue.chunks

            # Validate results
            if not chunks or len(chunks) == 0:
                raise ValueError(
                    f"Chunking failed: No chunks generated for dialogue {dialogue.ref_id}. "
                    f"Messages: {len(dialogue.context.msgs)}, "
                    f"Content length: {len(dialogue.content) if dialogue.content else 0}, "
                    f"Strategy: {self.chunker_config.chunker_strategy}"
                )

            logger.info(
                f"Successfully generated {len(chunks)} chunks for dialogue {dialogue.ref_id}. "
                f"Total characters processed: {len(dialogue.content) if dialogue.content else 0}"
            )
            
            return chunks
            
        except ValueError:
            # Re-raise validation errors
            raise
        except Exception as e:
            logger.error(
                f"Error processing dialogue {dialogue.ref_id} with strategy {self.chunker_strategy}: {e}",
                exc_info=True
            )
            raise

    def save_chunking_results(
        self, 
        chunks: List[Chunk], 
        dialogue: DialogData, 
        output_path: Optional[str] = None,
        preview_length: int = 100
    ) -> str:
        """Save the chunking results to a file and return the output path.

        Args:
            chunks: List of Chunk objects to save
            dialogue: The DialogData object that was processed
            output_path: Optional path to save the output (defaults to current directory)
            preview_length: Maximum length of content preview (default: 100)

        Returns:
            The path where the output was saved
            
        Raises:
            ValueError: If chunks or dialogue is invalid
            IOError: If file writing fails
        """
        # Validate input
        if not chunks:
            raise ValueError("chunks list cannot be empty")
        if not dialogue:
            raise ValueError("dialogue cannot be None")
        
        # Generate default output path if not provided
        if not output_path:
            output_dir = Path(__file__).parent.parent.parent
            output_path = str(output_dir / f"chunker_output_{self.chunker_strategy.lower()}.txt")
        
        logger.info(f"Saving chunking results to: {output_path}")
        
        try:
            # Prepare output content
            output_lines = [
                f"=== Chunking Results ({self.chunker_strategy}) ===",
                f"Dialogue ID: {dialogue.ref_id}",
                f"Original conversation has {len(dialogue.context.msgs) if dialogue.context else 0} messages",
                f"Total characters: {len(dialogue.content) if dialogue.content else 0}",
                f"Generated {len(chunks)} chunks:",
                ""
            ]
            
            for i, chunk in enumerate(chunks, 1):
                content_preview = chunk.content[:preview_length] if chunk.content else ""
                if len(chunk.content) > preview_length:
                    content_preview += "..."
                
                output_lines.append(f"  Chunk {i}: {len(chunk.content)} characters")
                output_lines.append(f"    Content preview: {content_preview}")
                if chunk.metadata:
                    output_lines.append(f"    Metadata: {chunk.metadata}")
                output_lines.append("")

            # Write to file
            with open(output_path, "w", encoding="utf-8") as f:
                f.write("\n".join(output_lines))

            logger.info(f"Successfully saved chunking results to: {output_path}")
            return output_path
            
        except IOError as e:
            logger.error(f"Failed to write chunking results to {output_path}: {e}", exc_info=True)
            raise
        except Exception as e:
            logger.error(f"Unexpected error saving chunking results: {e}", exc_info=True)
            raise


