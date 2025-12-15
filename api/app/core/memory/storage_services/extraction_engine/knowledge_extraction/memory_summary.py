import os
import asyncio
from datetime import datetime
from typing import List, Optional

from pydantic import Field, field_validator

from app.core.logging_config import get_memory_logger
from app.core.memory.models.message_models import DialogData

logger = get_memory_logger(__name__)
from app.core.memory.models.graph_models import MemorySummaryNode
from app.core.memory.models.base_response import RobustLLMResponse
from app.core.models.base import RedBearModelConfig
from app.core.memory.llm_tools.openai_embedder import OpenAIEmbedderClient
from app.core.memory.utils.config.config_utils import get_embedder_config
from app.core.memory.utils.prompt.prompt_utils import render_memory_summary_prompt
from uuid import uuid4


class MemorySummaryResponse(RobustLLMResponse):
    """Structured response for summary generation per chunk.

    This model ensures the LLM returns a valid, non-empty summary.
    Inherits robust validation from RobustLLMResponse.
    """
    summary: str = Field(
        ...,
        description="Concise memory summary for a single chunk. Must be a meaningful, non-empty string.",
        min_length=1,
        max_length=5000
    )


async def _process_chunk_summary(
    dialog: DialogData,
    chunk,
    llm_client,
    embedder: OpenAIEmbedderClient,
) -> Optional[MemorySummaryNode]:
    """Process a single chunk to generate a memory summary node."""
    # Skip empty chunks
    if not chunk.content or not chunk.content.strip():
        return None

    try:
        # Render prompt via Jinja2 for a single chunk
        prompt_content = await render_memory_summary_prompt(
            chunk_texts=chunk.content,
            json_schema=MemorySummaryResponse.model_json_schema(),
            max_words=200,
        )

        messages = [
            {"role": "system", "content": "You are an expert memory summarizer."},
            {"role": "user", "content": prompt_content},
        ]

        # Generate structured summary with the existing LLM client
        structured = await llm_client.response_structured(
            messages=messages,
            response_model=MemorySummaryResponse,
        )
        summary_text = structured.summary.strip()

        # Embed the summary
        embedding = (await embedder.response([summary_text]))[0]

        # Build node per chunk
        node = MemorySummaryNode(
            id=uuid4().hex,
            name=f"MemorySummaryChunk_{chunk.id}",
            group_id=dialog.group_id,
            user_id=dialog.user_id,
            apply_id=dialog.apply_id,
            run_id=dialog.run_id,  # 使用 dialog 的 run_id
            created_at=datetime.now(),
            expired_at=datetime(9999, 12, 31),
            dialog_id=dialog.id,
            chunk_ids=[chunk.id],
            content=summary_text,
            summary_embedding=embedding,
            metadata={"ref_id": dialog.ref_id},
            config_id=dialog.config_id,  # 添加 config_id
        )
        return node

    except Exception as e:
        # Log the error but continue processing other chunks
        logger.warning(f"Failed to generate summary for chunk {chunk.id} in dialog {dialog.id}: {e}", exc_info=True)
        return None


async def Memory_summary_generation(
    chunked_dialogs: List[DialogData],
    llm_client,
    embedding_id,
) -> List[MemorySummaryNode]:
    """Generate memory summaries per chunk, embed them, and return nodes."""
    embedder_cfg_dict = get_embedder_config(embedding_id)
    embedder = OpenAIEmbedderClient(
        model_config=RedBearModelConfig.model_validate(embedder_cfg_dict),
    )

    # Collect all tasks for parallel processing
    tasks = []
    for dialog in chunked_dialogs:
        for chunk in dialog.chunks:
            tasks.append(_process_chunk_summary(dialog, chunk, llm_client, embedder))

    # Process all chunks in parallel
    results = await asyncio.gather(*tasks, return_exceptions=False)

    # Filter out None values (failed or empty chunks)
    nodes = [node for node in results if node is not None]

    return nodes
