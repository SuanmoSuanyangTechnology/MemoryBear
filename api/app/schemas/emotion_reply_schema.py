"""情绪回应对外接口的请求与响应数据。"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.schemas.response_schema import ApiResponse


class EmotionReplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: Annotated[
        str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=2000)
    ]


class EmotionReplyData(BaseModel):
    emotion_code: str | None = None
    score: float | None = None
    tone: str | None = None
    do: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)
    prompt_injection: str | None = None


class EmotionReplyResponse(ApiResponse):
    data: EmotionReplyData
