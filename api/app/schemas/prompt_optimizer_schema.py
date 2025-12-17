from pydantic import BaseModel, Field
from uuid import UUID


# =========================================
# API Request Schemas
# =========================================
class PromptOptMessage(BaseModel):
    model_id: UUID = Field(
        ...,
        description="Model ID"
    )
    message: str = Field(
        ...,
        min_length=1,
        description="User's input message"
    )

    current_prompt: str = Field(
        default="",
        description="currently optimized prompt"
    )


class PromptOptModelSet(BaseModel):
    id: UUID | None = Field(
        default=None,
        description="Configuration ID"
    )

    system_prompt: str = Field(
        ...,
        description="System Prompt"
    )


# =========================================
# Service Layer Results
# =========================================
class OptimizePromptResult(BaseModel):
    prompt: str = Field(
        ...,
        description="Optimized Prompt"
    )
    desc: str = Field(
        ...,
        description="Description"
    )


# =========================================
# API Response Schemas
# =========================================
class CreateSessionResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID = Field(
        ...,
        description="Session ID"
    )


class OptimizePromptResponse(BaseModel):
    model_config = {"from_attributes": True}

    prompt: str = Field(
        ...,
        description="Optimized Prompt"
    )
    desc: str = Field(
        ...,
        description="Description"
    )
    variables: list = Field(
        ...,
        description="Variables"
    )


class SessionMessage(BaseModel):
    role: str = Field(
        ...,
        description="Message role (user/assistant)"
    )
    content: str = Field(
        ...,
        description="Message content"
    )


class SessionHistoryResponse(BaseModel):
    session_id: UUID = Field(
        ...,
        description="Session ID"
    )
    messages: list[SessionMessage] = Field(
        ...,
        description="List of messages in the session"
    )
