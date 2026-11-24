"""One-shot legacy metadata migration, ported from the API reflection engine.

The API converts a user's legacy ``meta_data`` into the v1 schema
(``identity_background`` / ``relationships`` / ``domain_specific_info``) with a
structured LLM call before an end-user merge. This module owns the pure part:
the response schema, response application and prompt rendering. Committing the
result under the user's write lock stays in the merge service.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict
from task_bridge.metadata_migration import (
    apply_migration_response_operations,
    legacy_metadata_input,
    new_metadata,
)

from src.prompts.metadata_migration import render_migration_prompt

logger = logging.getLogger(__name__)


class MigrationOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: str
    field: str
    value: str


class MigrationItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity_id: str
    operations: list[MigrationOperation]
    dropped_ops_count: Literal[0]


class MigrationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[MigrationItem]


def apply_migration_response(
    existing: object,
    raw: object,
    user_id: str,
) -> tuple[dict[str, object], int, int]:
    """Apply one parsed model response after checking its user identity."""

    response = (
        raw if isinstance(raw, MigrationResponse) else MigrationResponse.model_validate(raw)
    )
    if len(response.items) != 1:
        raise ValueError("迁移返回用户不匹配")
    return apply_migration_response_operations(
        existing,
        [operation.model_dump() for operation in response.items[0].operations],
        response.items[0].entity_id,
        user_id,
    )


async def prepare_migration_metadata(
    end_user_id: str,
    original: object,
    llm_client: Any,
    language: str = "zh",
) -> tuple[dict[str, object], int, int]:
    """Convert legacy metadata without committing it; return ``(merged, applied, rejected)``."""

    # NULL is legitimate empty history; any other non-object must not pose as it.
    if original is not None and not isinstance(original, dict):
        raise ValueError("历史 metadata 类型损坏")
    existing = original if original is not None else {}
    legacy = legacy_metadata_input(existing)
    if not legacy:
        return new_metadata(existing), 0, 0

    prompt = render_migration_prompt(
        entity_id=end_user_id,
        language=language,
        input_json=json.dumps(
            {"entity_id": end_user_id, **legacy}, ensure_ascii=False
        ),
    )
    raw = await llm_client.call_structured(
        [{"role": "user", "content": prompt}],
        MigrationResponse,
    )
    return apply_migration_response(existing, raw, end_user_id)


__all__ = [
    "MigrationItem",
    "MigrationOperation",
    "MigrationResponse",
    "apply_migration_response",
    "prepare_migration_metadata",
]