"""EmotionExtractionStep — sidecar step for extracting emotion from statements.

Replaces the legacy ``EmotionExtractionService`` with the unified ExtractionStep
paradigm.  Registered via ``@SidecarStepFactory.register`` so the orchestrator
picks it up automatically when ``emotion_enabled`` is ``True``.
"""

import logging
from typing import Any

from app.core.memory.models.emotion_models import EmotionExtraction
from app.core.memory.utils.prompt.prompt_utils import render_emotion_extraction_prompt

from .base import ExtractionStep, StepContext
from ..sidecar_factory import SidecarStepFactory, SidecarTiming
from .schema import EmotionStepInput, EmotionStepOutput

logger = logging.getLogger(__name__)


@SidecarStepFactory.register("emotion_enabled", SidecarTiming.AFTER_STATEMENT)
class EmotionExtractionStep(ExtractionStep[EmotionStepInput, EmotionStepOutput]):
    """Extract emotion type, intensity, and keywords from a statement.

    This is a **sidecar** (non-critical) step — failure returns a neutral
    default without aborting the pipeline.

    The step self-registers with ``SidecarStepFactory`` under the config key
    ``emotion_enabled`` and timing ``AFTER_STATEMENT``.
    """

    def __init__(self, context: StepContext) -> None:
        super().__init__(context)
        # Emotion-specific config flags (may live on a MemoryConfig object
        # attached to context.config or as top-level attributes).
        self.extract_keywords = getattr(self.config, "emotion_extract_keywords", True)
        self.enable_subject = getattr(self.config, "emotion_enable_subject", False)

    # ── Identity ──

    @property
    def name(self) -> str:
        return "emotion_extraction"

    @property
    def is_critical(self) -> bool:
        return False

    # ── Config-driven skip ──

    def should_skip(self) -> bool:
        return not getattr(self.config, "emotion_enabled", False)

    # ── Lifecycle ──

    async def render_prompt(self, input_data: EmotionStepInput) -> str:
        return await render_emotion_extraction_prompt(
            statement=input_data.statement_text,
            extract_keywords=self.extract_keywords,
            enable_subject=self.enable_subject,
            language=self.language,
        )

    async def call_llm(self, prompt: Any) -> Any:
        messages = [{"role": "user", "content": prompt}]
        return await self.call_structured(
            messages, EmotionExtraction
        )

    async def parse_response(
        self, raw_response: Any, input_data: EmotionStepInput
    ) -> EmotionStepOutput:
        return EmotionStepOutput(
            emotion_type=getattr(raw_response, "emotion_type", "neutral"),
            emotion_intensity=getattr(raw_response, "emotion_intensity", 0.0),
            emotion_keywords=getattr(raw_response, "emotion_keywords", []),
        )

    def get_default_output(self) -> EmotionStepOutput:
        return EmotionStepOutput()
