"""Schemas for the ``/memory`` endpoint this service still serves.

The write and emotion-reply request models moved out with their endpoints
(memory-jobs owns the write path; the API keeps its own emotion reply). What
remains is the merge request and the legacy success envelope it returns.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field

from src.common.timing import now_ms


class WriteApiResponse(BaseModel):
    """The API's untyped ``ApiResponse`` envelope (legacy wire shape)."""

    code: int = 0
    msg: str = "OK"
    data: Any | None = None
    error: str = ""
    time: int = Field(default_factory=now_ms)


class MergeUserInput(BaseModel):
    """Host ``MergeEndUserInput`` for ``POST /v1/memory/merge``."""

    end_user_ids: set[uuid.UUID] = Field()
    target: uuid.UUID = Field(...)


__all__ = [
    "MergeUserInput",
    "WriteApiResponse",
]