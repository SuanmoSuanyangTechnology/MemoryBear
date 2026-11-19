"""Structured-output fallback shared by the retrieval prompts.

Ported from the host's ``app.core.models.llm.StructResponse``: when a provider
cannot enforce a tool schema, the completion text is repaired into JSON and
validated against the target Pydantic model. Keeping it here (instead of a
model-client dependency) lets the query-splitting and summary steps stay pure
application logic.
"""

from __future__ import annotations

from typing import Any

import json_repair
from langchain_core.messages import AIMessage


class StructResponse:
    """Parse a chat response into a structured object with repaired JSON."""

    def __init__(self, schema: dict[str, Any] | type) -> None:
        self.schema = schema

    def __ror__(self, other: AIMessage | str) -> Any:
        return self._convert(self.extract_text(other))

    @staticmethod
    def extract_text(other: AIMessage | str) -> str:
        if isinstance(other, str):
            return other
        if isinstance(other, AIMessage):
            return other.text
        raise RuntimeError(f"Unsupported struct type {type(other).__name__}")

    def _convert(self, text: str) -> Any:
        fixed_json = json_repair.repair_json(text, return_objects=True)
        schema = self.schema
        if isinstance(schema, type) and hasattr(schema, "model_validate"):
            return schema.model_validate(fixed_json)
        return fixed_json


__all__ = ["StructResponse"]