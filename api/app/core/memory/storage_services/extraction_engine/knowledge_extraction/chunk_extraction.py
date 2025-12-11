import os
from typing import Optional

from app.core.logging_config import get_memory_logger
from app.core.memory.models.message_models import DialogData, Chunk
from app.core.memory.models.config_models import ChunkerConfig
from app.core.memory.llm_tools.chunker_client import ChunkerClient
from app.core.memory.utils.config.config_utils import get_chunker_config

logger = get_memory_logger(__name__)


class DialogueChunker:
    """A class that processes dialogues and fills them with chunks based on a specified strategy.

    This class encapsulates the chunking process, allowing for easy configuration and application
    of different chunking strategies to dialogue data.
    """

    def __init__(self, chunker_strategy: str = "RecursiveChunker", llm_client=None):
        """Initialize the DialogueChunker with a specific chunking strategy.

        Args:
            chunker_strategy: The chunking strategy to use (default: RecursiveChunker)
                             Options include: SemanticChunker, RecursiveChunker, LateChunker, NeuralChunker
        """
        self.chunker_strategy = chunker_strategy
        chunker_config_dict = get_chunker_config(chunker_strategy)
        self.chunker_config = ChunkerConfig.model_validate(chunker_config_dict)
        # 对于 LLMChunker，需要传入 llm_client
        if self.chunker_config.chunker_strategy == "LLMChunker":
            self.chunker_client = ChunkerClient(self.chunker_config, llm_client)
        else:
            self.chunker_client = ChunkerClient(self.chunker_config)

    async def process_dialogue(self, dialogue: DialogData) -> list[Chunk]:
        """Process a dialogue by generating chunks and adding them to the DialogData object.

        Args:
            dialogue: The DialogData object to process

        Returns:
            A list of Chunk objects
        """
        result_dialogue = await self.chunker_client.generate_chunks(dialogue)
        # Defensive fallback: ensure at least one chunk is returned for non-empty content
        try:
            chunks = result_dialogue.chunks
        except Exception:
            chunks = []

        if not chunks or len(chunks) == 0:
            # If the dialogue has content, return a single fallback chunk built from messages
            content_str = getattr(result_dialogue, "content", "") or getattr(dialogue, "content", "")
            if content_str and len(content_str.strip()) > 0:
                fallback_chunk = Chunk.from_messages(
                    dialogue.context.msgs,
                    metadata={
                        "fallback": "single_chunk",
                        "chunker_strategy": self.chunker_config.chunker_strategy,
                        "source": "DialogueChunkerFallback",
                    },
                )
                return [fallback_chunk]
            # No content: return empty list
            return []

        return chunks

    def save_chunking_results(self, dialogue: DialogData, output_path: Optional[str] = None) -> str:
        """Save the chunking results to a file and return the output path.

        Args:
            dialogue: The processed DialogData object with chunks
            output_path: Optional path to save the output (default: chunker_output_{strategy}.txt)

        Returns:
            The path where the output was saved
        """
        if not output_path:
            output_path = os.path.join(os.path.dirname(__file__), "..", "..",
                                      f"chunker_output_{self.chunker_strategy.lower()}.txt")

        output_lines = []
        output_lines.append(f"=== Chunking Results ({self.chunker_strategy}) ===")
        output_lines.append(f"Dialogue ID: {dialogue.ref_id}")
        output_lines.append(f"Original conversation has {len(dialogue.context.msgs)} messages")
        output_lines.append(f"Total characters: {len(dialogue.content)}")

        output_lines.append(f"Generated {len(dialogue.chunks)} chunks:")
        for i, chunk in enumerate(dialogue.chunks):
            output_lines.append(f"  Chunk {i+1}: {len(chunk.content)} characters")
            output_lines.append(f"    Content preview: {chunk.content}...")
            if chunk.metadata:
                output_lines.append(f"    Metadata: {chunk.metadata}")

        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(output_lines))

        logger.info(f"Chunking results saved to: {output_path}")
        return output_path


