"""Health response contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ComponentHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["up", "down", "timeout"]
    latency_ms: int = Field(ge=0)
    error_type: str | None = None


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["alive", "ready", "not_ready"]
    service: Literal["mem-knowledge"] = "mem-knowledge"
    process_role: str
    checked_at_ms: int = Field(ge=0)
    trace_id: str
    code: str | None = None
    retryable: bool | None = None
    components: dict[str, ComponentHealth] | None = None
