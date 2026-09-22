"""Scene boundary and SceneSummary schemas."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SceneBoundary = Literal["CONTINUE", "SHIFTED", "BERT_FAILED_CONTINUE"]
SceneCloseReason = Literal["SHIFTED", "IDLE_TIMEOUT"]
SceneDemoPrediction = Literal["CONTINUE", "SHIFTED"]
SceneShortCircuit = Literal["first_scene", "min_turns", "max_turns"]

SceneThreshold = Annotated[float, Field(ge=0.5, le=0.95)]
SceneHistoryWindowSize = Annotated[int, Field(ge=1, le=4)]
SceneMinTurns = Annotated[int, Field(ge=0, le=10)]
SceneMaxTurns = Annotated[int, Field(ge=5, le=100)]
SceneIdleTimeoutSeconds = Annotated[int, Field(ge=60, le=30 * 24 * 60 * 60)]
SceneMinCharsToSummary = Annotated[int, Field(ge=0, le=200)]
SceneTimeDecayPenalty = Annotated[float, Field(ge=0, le=0.2)]


def validate_scene_turn_range(scene_min_turns: int, scene_max_turns: int) -> None:
    if scene_min_turns >= scene_max_turns:
        raise ValueError("scene_min_turns must be less than scene_max_turns")


class SceneConfig(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    config_id: UUID
    scene_threshold: SceneThreshold = 0.8
    scene_history_window_size: SceneHistoryWindowSize = 4
    scene_min_turns: SceneMinTurns = 2
    scene_max_turns: SceneMaxTurns = 10
    scene_idle_timeout_seconds: SceneIdleTimeoutSeconds = 86400
    scene_min_chars_to_summary: SceneMinCharsToSummary = 0
    time_decay_penalty: SceneTimeDecayPenalty = 0.1

    @model_validator(mode="after")
    def validate_scene_ranges(self):
        validate_scene_turn_range(self.scene_min_turns, self.scene_max_turns)
        return self


class SceneConfigUpdate(SceneConfig):
    pass


class SceneContext(BaseModel):
    resolved_end_user_id: str
    current_message_id: str
    current_content: str
    previous_shifted_message_id: str | None = None
    history_user_messages: list[str] = Field(default_factory=list)
    direct_decision: SceneBoundary | None = None


class SceneMessage(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime


class SceneSummaryContent(BaseModel):
    content: str = Field(..., min_length=1)


class GenerateSceneSummaryTask(BaseModel):
    end_user_id: str
    config_id: str
    scene_start_message_id: str
    close_before_message_id: str | None = None
    idle_high_watermark_message_id: str | None = None
    close_reason: SceneCloseReason


class SceneSplitDemoRequest(BaseModel):
    """BERT 场景切分演示请求；未传的配置项由接口回落到 config_id 对应配置。

    当前场景轮次不由前端传入，由接口按「历史窗口数 + 1」推导，仅用于演示短路分支。
    """

    config_id: UUID | int
    history_messages: list[str] = Field(default_factory=list)
    current_query: str
    scene_threshold: SceneThreshold | None = None
    scene_history_window_size: SceneHistoryWindowSize | None = None
    scene_min_turns: SceneMinTurns | None = None
    scene_max_turns: SceneMaxTurns | None = None

    @field_validator("current_query")
    @classmethod
    def strip_current_query(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("current_query must not be blank")
        return text

    @model_validator(mode="after")
    def validate_provided_turn_range(self):
        if self.scene_min_turns is not None and self.scene_max_turns is not None:
            validate_scene_turn_range(self.scene_min_turns, self.scene_max_turns)
        return self


class SceneSummaryDemo(BaseModel):
    """演示用 Scene 摘要区文案（状态标签由前端按 prediction 枚举）。"""

    text: str
    hint: str


class SceneSplitDemoResponse(BaseModel):
    """BERT 场景切分演示响应。"""

    score: float
    threshold: float
    prediction: SceneDemoPrediction
    short_circuit: SceneShortCircuit | None = None
    window_size: int
    input_chars: int
    sequence: str
    reason: str
    scene_summary: SceneSummaryDemo
