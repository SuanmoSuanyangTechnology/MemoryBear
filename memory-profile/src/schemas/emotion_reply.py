"""Request and result shapes for emotion replies."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


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


class EmotionPolicy(BaseModel):
    tone: str
    do: list[str]
    avoid: list[str]


class AppEmotionPolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_id: UUID
    emotion_code: str = Field(min_length=1, max_length=50)


class AppEmotionStrategy(BaseModel):
    ontology_id: str
    ontology_source: Literal["custom", "system_default"]
    emotion_code: str
    tone: str
    do_list: list[str]
    avoid_list: list[str]


class AppEmotionPolicyData(BaseModel):
    available: bool
    strategy: AppEmotionStrategy | None
