"""Scene split demo request/response contracts (M-R5)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SceneDemoPrediction = Literal["CONTINUE", "SHIFTED"]
SceneShortCircuit = Literal["first_scene", "min_turns", "max_turns"]


class SceneSummaryDemo(BaseModel):
    text: str
    hint: str


class SceneSplitDemoResponse(BaseModel):
    score: float
    threshold: float
    prediction: SceneDemoPrediction
    short_circuit: SceneShortCircuit | None = None
    window_size: int
    input_chars: int
    sequence: str
    reason: str
    scene_summary: SceneSummaryDemo


class SceneConfigFields(BaseModel):
    """The Scene fields the demo falls back to when the request omits them."""

    config_id: str
    scene_threshold: float = Field(ge=0.5, le=0.95)
    scene_history_window_size: int = Field(ge=1, le=4)
    scene_min_turns: int = Field(ge=0, le=10)
    scene_max_turns: int = Field(ge=5, le=100)
