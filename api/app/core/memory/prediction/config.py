import os
from dataclasses import dataclass
from pathlib import Path

from .budget import Limits, limits_for


@dataclass(frozen=True)
class PredictionSettings:
    out_dir: Path
    max_turns: int
    context_tokens: int

    @property
    def limits(self) -> Limits:
        return limits_for(self.context_tokens)


def load_prediction_settings() -> PredictionSettings:
    return PredictionSettings(
        out_dir=Path(os.getenv("PREDICTION_OUTPUT_DIR", "logs/prediction-output")),
        max_turns=max(3, min(6, int(os.getenv("PREDICTION_MAX_TURNS", "4")))),
        context_tokens=max(4_000, int(os.getenv("PREDICTION_CONTEXT_TOKENS", "128000"))),
    )
