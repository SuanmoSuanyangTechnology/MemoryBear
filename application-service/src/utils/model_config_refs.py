"""Model reference collection for configuration snapshots and draft
publication; mirrors the host ``model_impact_service`` helpers.

Reference surface = the default-model column plus ``model_id`` / ``reranker_id``
values anywhere in the configuration JSON (invalid UUID references are skipped).
"""

from __future__ import annotations

import uuid
from typing import Any

CONFIG_MODEL_REF_KEYS = frozenset({"model_id", "reranker_id"})


def config_model_refs(config: Any) -> set[str]:
    """Recursively collect model references from the configuration JSON
    (shared by published snapshots and draft publication).

    `model_id`: llm/agent/classifier/extractor top level + knowledge-base
    metadata_model nesting;
    `reranker_id`: knowledge-retrieval nodes and Agent knowledge-base tools
    (resolved to a key via rerank at runtime).
    """
    found: set[str] = set()
    if isinstance(config, dict):
        for key, value in config.items():
            if key in CONFIG_MODEL_REF_KEYS and isinstance(value, str) and value:
                found.add(value)
            else:
                found |= config_model_refs(value)
    elif isinstance(config, list):
        for item in config:
            found |= config_model_refs(item)
    return found


def config_ref_ids(
    config: Any,
    default_model_config_id: uuid.UUID | None = None,
) -> list[uuid.UUID]:
    """Collect model reference ids for a snapshot (default-model column + JSON
    reference keys): de-duplicated, order-preserving; invalid UUIDs are skipped.

    Shared by publish, rollback, and workflow-publish-as-tool activation paths;
    same reference set as the impact scan.
    """
    ref_ids: list[uuid.UUID] = []
    if default_model_config_id is not None:
        ref_ids.append(default_model_config_id)
    for raw in config_model_refs(config):
        try:
            parsed = uuid.UUID(str(raw))
        except (TypeError, ValueError):
            continue
        if parsed not in ref_ids:
            ref_ids.append(parsed)
    return ref_ids


__all__ = ["CONFIG_MODEL_REF_KEYS", "config_model_refs", "config_ref_ids"]
