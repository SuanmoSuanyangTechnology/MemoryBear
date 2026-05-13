"""EmbeddingStep — generates vector embeddings for statements, chunks, dialogs, and entities.

Unlike the LLM-based ExtractionSteps, EmbeddingStep calls an embedder client
rather than an LLM.  It still follows the ``should_skip`` / ``run`` /
``get_default_output`` contract so the orchestrator can treat it uniformly.

Supports **partial** embedding runs — the caller can populate only the fields
it needs (e.g. only ``statement_texts``) and leave the rest empty.
"""

import asyncio
import logging
from typing import Any, Dict, List

from .schema import EmbeddingStepInput, EmbeddingStepOutput

logger = logging.getLogger(__name__)


class EmbeddingStep:
    """Generate vector embeddings for text inputs.

    This step does **not** inherit from ``ExtractionStep`` because it does not
    follow the render_prompt → call_llm → parse_response lifecycle.  It does,
    however, expose the same ``run`` / ``should_skip`` / ``get_default_output``
    interface so the orchestrator can use it interchangeably.

    Pilot-run mode skips execution entirely and returns empty dicts.
    """

    def __init__(
        self,
        embedder_client: Any,
        is_pilot_run: bool = False,
        batch_size: int = 100,
    ) -> None:
        self.embedder_client = embedder_client
        self.is_pilot_run = is_pilot_run
        self.batch_size = batch_size

    @property
    def name(self) -> str:
        return "embedding_generation"

    @property
    def is_critical(self) -> bool:
        return False

    @property
    def max_retries(self) -> int:
        return 1

    @property
    def retry_backoff_base(self) -> float:
        return 1.0

    def should_skip(self) -> bool:
        return self.is_pilot_run

    def get_default_output(self) -> EmbeddingStepOutput:
        return EmbeddingStepOutput()

    # ── Core execution ──

    async def run(self, input_data: EmbeddingStepInput) -> EmbeddingStepOutput:
        """Generate embeddings for all non-empty text fields in *input_data*."""
        if self.should_skip():
            logger.info("EmbeddingStep skipped (pilot run)")
            return self.get_default_output()

        try:
            stmt_emb, chunk_emb, dialog_emb, entity_emb = await asyncio.gather(
                self._embed_dict(input_data.statement_texts),
                self._embed_dict(input_data.chunk_texts),
                self._embed_list(input_data.dialog_texts),
                self._embed_dict(input_data.entity_names),
            )
            return EmbeddingStepOutput(
                statement_embeddings=stmt_emb,
                chunk_embeddings=chunk_emb,
                dialog_embeddings=dialog_emb,
                entity_embeddings=entity_emb,
            )
        except Exception as exc:
            logger.warning("EmbeddingStep failed, returning empty output: %s", exc)
            return self.get_default_output()

    # ── Internal helpers ──

    async def _embed_dict(
        self, texts: Dict[str, str]
    ) -> Dict[str, List[float]]:
        """Embed a dict of ``{id: text}`` and return ``{id: embedding}``."""
        if not texts:
            return {}

        ids = list(texts.keys())
        text_list = list(texts.values())
        embeddings = await self._batch_embed(text_list)

        return dict(zip(ids, embeddings))

    async def _embed_list(self, texts: List[str]) -> List[List[float]]:
        """Embed a plain list of texts."""
        if not texts:
            return []
        return await self._batch_embed(texts)

    async def _batch_embed(self, texts: List[str]) -> List[List[float]]:
        """Call the embedder in batches of ``self.batch_size``."""
        if len(texts) <= self.batch_size:
            return await self.embedder_client.response(texts)

        batches = [
            texts[i : i + self.batch_size]
            for i in range(0, len(texts), self.batch_size)
        ]
        batch_results = await asyncio.gather(
            *(self.embedder_client.response(b) for b in batches)
        )
        embeddings: List[List[float]] = []
        for result in batch_results:
            embeddings.extend(result)
        return embeddings
