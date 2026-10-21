"""Model parameter payload.

Minimal closure of the monolith `app.schemas.app_schema.ModelParameters` (frozen
copy: defaults and constraints must stay identical to the host schema — the
structured column is shared with the monolith, so validation semantics are part
of the cross-side contract).
"""

from pydantic import BaseModel, Field


class ModelParameters(BaseModel):
    """Model parameter configuration."""

    temperature: float = Field(
        default=0.7, ge=0.0, le=2.0, description="Sampling temperature"
    )
    max_tokens: int = Field(default=2000, ge=1, le=32000, description="Max output tokens")
    top_p: float = Field(default=1.0, ge=0.0, le=1.0, description="Nucleus sampling parameter")
    frequency_penalty: float = Field(default=0.0, ge=-2.0, le=2.0, description="Frequency penalty")
    presence_penalty: float = Field(default=0.0, ge=-2.0, le=2.0, description="Presence penalty")
    n: int = Field(default=1, ge=1, le=10, description="Number of completions to generate")
    stop: list[str] | None = Field(default=None, description="Stop sequences")
    deep_thinking: bool = Field(
        default=False,
        description="Enable deep-thinking mode (requires a supporting model)",
    )
    thinking_budget_tokens: int | None = Field(
        default=None, ge=1, le=131072, description="Deep-thinking token budget (model dependent)"
    )
    json_output: bool = Field(
        default=False, description="Force JSON output (requires the json_output capability)"
    )
