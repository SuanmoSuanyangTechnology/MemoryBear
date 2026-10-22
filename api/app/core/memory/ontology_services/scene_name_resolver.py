"""Resolve stored domain IDs for model-facing metadata without changing storage."""

import asyncio
import logging
from uuid import UUID

from app.core.memory.models.metadata_models import is_scene_type_id
from app.db import get_db_context
from app.repositories.ontology_scene_type_repository import OntologySceneTypeRepository

logger = logging.getLogger(__name__)


def load_scene_names(metadata: object) -> dict[str, str]:
    domains = metadata.get("domain_specific_info") if isinstance(metadata, dict) else None
    if not isinstance(domains, dict):
        return {}
    ids: dict[str, UUID] = {}
    for key in domains:
        if is_scene_type_id(key):
            ids[key] = UUID(key)
    if not ids:
        return {}
    try:
        with get_db_context() as db:
            names = OntologySceneTypeRepository(db).get_names_by_ids(list(set(ids.values())))
        return {key: names[str(value)] for key, value in ids.items() if str(value) in names}
    except Exception:
        logger.warning("[Metadata] Scene name lookup failed; retaining stored keys", exc_info=True)
        return {}


async def load_scene_names_async(metadata: object) -> dict[str, str]:
    # Create and close the sync session inside the worker thread, not the event loop.
    return await asyncio.to_thread(load_scene_names, metadata)
