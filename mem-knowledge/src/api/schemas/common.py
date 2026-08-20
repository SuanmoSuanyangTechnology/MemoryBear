"""Common internal HTTP response contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class SuccessEnvelope[DataT](BaseModel):
    """Versioned internal success response."""

    data: DataT
    trace_id: str
    schema_version: Literal["1"] = "1"


__all__ = ["SuccessEnvelope"]
