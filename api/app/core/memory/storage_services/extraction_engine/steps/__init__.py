"""Extraction pipeline steps — unified ExtractionStep paradigm.

Importing this package triggers @register decorator self-registration
for all sidecar (non-critical) steps via SidecarStepFactory.
"""

from ..sidecar_factory import SidecarStepFactory, SidecarTiming  # noqa: F401

# Step implementations — importing triggers @register self-registration.
from .statement_temporal_step import StatementTemporalExtractionStep  # noqa: F401
from .triplet_step import TripletExtractionStep  # noqa: F401
from .emotion_step import EmotionExtractionStep  # noqa: F401
from .embedding_step import EmbeddingStep  # noqa: F401

# Refactored orchestrator
from app.core.memory.storage_services.extraction_engine.extraction_pipeline_orchestrator import NewExtractionOrchestrator  # noqa: F401
