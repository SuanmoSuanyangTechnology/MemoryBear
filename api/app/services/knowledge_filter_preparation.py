from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

async def get_metadata_defs_for_filtering_async(db: AsyncSession, knowledge_id: uuid.UUID) -> dict[str, dict]:
    """Async version of get_metadata_defs_for_filtering."""
    from app.models.knowledge_model import Knowledge
    from app.repositories.knowledge_metadata_repository import KnowledgeMetadataRepository
    from app.services.knowledge_builtin_resolver import BuiltinFieldResolver
    result = {}
    custom_fields = await KnowledgeMetadataRepository.get_by_knowledge_id_async(db, knowledge_id)
    for f in custom_fields:
        result[f.name] = {'id': f.id, 'type': f.type, 'is_builtin': False}
    knowledge = await db.get(Knowledge, knowledge_id)
    if knowledge and knowledge.builtin_metadata_enabled == 1:
        for bf in BuiltinFieldResolver.get_all():
            result[bf.name] = {'type': bf.type, 'is_builtin': True}
    return result

def get_common_metadata_defs(metadata_defs_by_kb: dict[uuid.UUID, dict[str, dict]]) -> dict[str, dict]:
    field_names = set()
    for metadata_defs in metadata_defs_by_kb.values():
        field_names.update(metadata_defs.keys())
    common_defs: dict[str, dict] = {}
    for field_name in field_names:
        common_type = None
        common_def = None
        for metadata_defs in metadata_defs_by_kb.values():
            field_def = metadata_defs.get(field_name)
            if not field_def:
                common_def = None
                break
            if common_type is None:
                common_type = field_def['type']
                common_def = field_def
            elif common_type != field_def['type']:
                common_def = None
                break
        if common_def:
            common_defs[field_name] = dict(common_def)
    return common_defs
