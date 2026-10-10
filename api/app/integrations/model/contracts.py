"""Transport-neutral model service call contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

MODEL_SOURCE_INTERNAL_API = "in_api"
MODEL_SOURCE_PLATFORM_ADMIN = "platform_admin"


@dataclass(frozen=True, slots=True)
class ModelCallContext:
    """Identity and trace metadata for one model service call."""

    actor_id: UUID | None
    actor_name: str | None
    tenant_id: UUID
    workspace_id: UUID | None
    trace_id: str
    source: str = MODEL_SOURCE_INTERNAL_API


@dataclass(frozen=True, slots=True)
class ModelServiceCallResult:
    """Buffered outcome of one internal model service call.

    ``payload`` is the parsed response envelope（``{code,msg,data,error,time}``）when
    the body is a JSON object, ``None`` otherwise——调用方据 status_code + code 判定。
    """

    status_code: int
    payload: dict[str, Any] | None
