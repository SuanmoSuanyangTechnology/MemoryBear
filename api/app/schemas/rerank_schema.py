"""Rerank strategy request contracts."""

from __future__ import annotations

import math
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RerankMode(StrEnum):
    RERANKING_MODEL = "reranking_model"
    WEIGHTED_SCORE = "weighted_score"


class RerankWeights(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    semantic_weight: float = Field(default=0.7, ge=0, le=1)
    participle_weight: float = Field(default=0.3, ge=0, le=1)

    @model_validator(mode="after")
    def validate_weights(self) -> RerankWeights:
        values = (self.semantic_weight, self.participle_weight)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("rerank weights must be finite")
        if not math.isclose(sum(values), 1.0, abs_tol=1e-6):
            raise ValueError("rerank weights must sum to 1")
        return self


__all__ = ["RerankMode", "RerankWeights"]
