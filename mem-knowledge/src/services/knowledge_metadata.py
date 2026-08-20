"""Knowledge metadata behavior copied from the legacy async service."""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import KnowledgeError
from ..models.owned import (
    Document,
    Knowledge,
    KnowledgeMetadata,
    KnowledgeMetadataBinding,
)
from ..rag.metadata import BuiltinFieldResolver
from ..repositories.knowledge_metadata import KnowledgeMetadataRepository


class KnowledgeMetadataService:
    repository = KnowledgeMetadataRepository
    BUILTIN_FIELD_NAMES = {field.name for field in BuiltinFieldResolver.get_all()}

    @staticmethod
    async def list_metadata_fields_async(
        db: AsyncSession,
        knowledge_id: uuid.UUID,
    ) -> dict:
        return await KnowledgeMetadataService.list_metadata_fields_for_knowledge_ids_async(
            db,
            [knowledge_id],
            include_builtin_when_disabled=True,
            preserve_single_ids=True,
        )

    @staticmethod
    async def list_metadata_fields_for_knowledge_ids_async(
        db: AsyncSession,
        knowledge_ids: list[uuid.UUID],
        *,
        include_builtin_when_disabled: bool = False,
        preserve_single_ids: bool = False,
        include_counts: bool = True,
    ) -> dict:
        unique_knowledge_ids = list(dict.fromkeys(knowledge_ids))
        if not unique_knowledge_ids:
            return {
                "custom": [],
                "builtin_enabled": False,
                "builtin_fields": [],
            }

        custom_fields = await KnowledgeMetadataService.repository.get_by_knowledge_ids_async(
            db,
            unique_knowledge_ids,
        )
        fields_by_kb = {knowledge_id: [] for knowledge_id in unique_knowledge_ids}
        for field in custom_fields:
            fields_by_kb.setdefault(field.knowledge_id, []).append(field)

        counts_by_metadata_id: dict[uuid.UUID, int] = {}
        if include_counts:
            repository = KnowledgeMetadataService.repository
            counts_by_metadata_id = (
                await repository.count_active_bindings_by_metadata_ids_async(
                    db,
                    [field.id for field in custom_fields],
                )
            )

        result = await db.execute(
            select(Knowledge.id, Knowledge.builtin_metadata_enabled).where(
                Knowledge.id.in_(unique_knowledge_ids)
            )
        )
        builtin_enabled_by_kb = {
            row[0]: row[1] == 1 for row in result.all()
        }
        for knowledge_id in unique_knowledge_ids:
            builtin_enabled_by_kb.setdefault(knowledge_id, False)

        return KnowledgeMetadataService._build_common_metadata_fields_response(
            fields_by_kb,
            builtin_enabled_by_kb,
            counts_by_metadata_id,
            include_builtin_when_disabled=include_builtin_when_disabled,
            preserve_single_ids=preserve_single_ids,
        )

    @staticmethod
    def _build_common_metadata_fields_response(
        fields_by_kb: dict[uuid.UUID, list[KnowledgeMetadata]],
        builtin_enabled_by_kb: dict[uuid.UUID, bool],
        counts_by_metadata_id: dict[uuid.UUID, int],
        *,
        include_builtin_when_disabled: bool = False,
        preserve_single_ids: bool = False,
    ) -> dict:
        knowledge_ids = list(fields_by_kb)
        common_keys: set[tuple[str, str]] | None = None
        field_lookup_by_kb: dict[
            uuid.UUID,
            dict[tuple[str, str], KnowledgeMetadata],
        ] = {}
        for knowledge_id, fields in fields_by_kb.items():
            lookup = {(field.name, field.type): field for field in fields}
            field_lookup_by_kb[knowledge_id] = lookup
            keys = set(lookup)
            common_keys = keys if common_keys is None else common_keys & keys

        common_keys = common_keys or set()
        first_kb_id = knowledge_ids[0] if knowledge_ids else None
        first_kb_fields = fields_by_kb.get(first_kb_id, []) if first_kb_id else []
        ordered_keys = [
            (field.name, field.type)
            for field in first_kb_fields
            if (field.name, field.type) in common_keys
        ]

        single_kb = len(knowledge_ids) == 1
        custom_fields = []
        for key in ordered_keys:
            fields = [field_lookup_by_kb[kb_id][key] for kb_id in knowledge_ids]
            first_field = fields[0]
            custom_field = {
                "id": first_field.id if single_kb and preserve_single_ids else None,
                "type": first_field.type,
                "name": first_field.name,
                "is_builtin": False,
                "count": sum(
                    counts_by_metadata_id.get(field.id, 0) for field in fields
                ),
            }
            if single_kb and preserve_single_ids:
                custom_field["created_at"] = first_field.created_at
                custom_field["updated_at"] = first_field.updated_at
            custom_fields.append(custom_field)

        builtin_enabled = (
            all(
                builtin_enabled_by_kb.get(knowledge_id, False)
                for knowledge_id in knowledge_ids
            )
            if knowledge_ids
            else False
        )
        return {
            "custom": custom_fields,
            "builtin_enabled": builtin_enabled,
            "builtin_fields": (
                BuiltinFieldResolver.get_all()
                if builtin_enabled or include_builtin_when_disabled
                else []
            ),
        }

    @staticmethod
    async def create_metadata_field_async(
        db: AsyncSession,
        knowledge_id: uuid.UUID,
        name: str,
        field_type: str,
        tenant_id: uuid.UUID,
        created_by: uuid.UUID,
    ) -> KnowledgeMetadata:
        if name in KnowledgeMetadataService.BUILTIN_FIELD_NAMES:
            raise KnowledgeError.from_code(
                "KB_VALIDATION_ERROR",
                f"Metadata field '{name}' conflicts with a builtin field",
            )
        existing = await KnowledgeMetadataService.repository.get_by_name_async(
            db,
            knowledge_id,
            name,
        )
        if existing:
            raise KnowledgeError.from_code(
                "KB_CONFLICT",
                f"Metadata field '{name}' already exists",
            )
        field = KnowledgeMetadata(
            tenant_id=tenant_id,
            knowledge_id=knowledge_id,
            name=name,
            type=field_type,
            created_by=created_by,
            updated_by=created_by,
        )
        return await KnowledgeMetadataService.repository.create_async(db, field)

    @staticmethod
    async def update_metadata_field_async(
        db: AsyncSession,
        metadata_id: uuid.UUID,
        knowledge_id: uuid.UUID,
        name: str | None,
        updated_by: uuid.UUID,
    ) -> KnowledgeMetadata:
        field = await KnowledgeMetadataService.repository.get_by_id_async(db, metadata_id)
        if field is None or field.knowledge_id != knowledge_id:
            raise KnowledgeError.from_code(
                "KB_RESOURCE_NOT_FOUND",
                "Metadata field not found",
            )
        update_data = {"updated_by": updated_by}
        if name and name != field.name:
            if name in KnowledgeMetadataService.BUILTIN_FIELD_NAMES:
                raise KnowledgeError.from_code(
                    "KB_VALIDATION_ERROR",
                    f"Metadata field '{name}' conflicts with a builtin field",
                )
            existing = await KnowledgeMetadataService.repository.get_by_name_async(
                db,
                knowledge_id,
                name,
            )
            if existing and existing.id != metadata_id:
                raise KnowledgeError.from_code(
                    "KB_CONFLICT",
                    f"Metadata field '{name}' already exists",
                )
            update_data["name"] = name
        await KnowledgeMetadataService.repository.update_async(
            db,
            metadata_id,
            update_data,
        )
        await db.refresh(field)
        return field

    @staticmethod
    async def delete_metadata_field_async(
        db: AsyncSession,
        metadata_id: uuid.UUID,
        knowledge_id: uuid.UUID,
    ) -> None:
        field = await KnowledgeMetadataService.repository.get_by_id_async(db, metadata_id)
        if field is None or field.knowledge_id != knowledge_id:
            raise KnowledgeError.from_code(
                "KB_RESOURCE_NOT_FOUND",
                "Metadata field not found",
            )
        try:
            await db.execute(
                delete(KnowledgeMetadataBinding).where(
                    KnowledgeMetadataBinding.metadata_id == metadata_id
                )
            )
            await db.execute(
                update(Document)
                .where(Document.kb_id == knowledge_id)
                .values({Document.meta_data: Document.meta_data.op("-")(field.name)})
                .execution_options(synchronize_session=False)
            )
            await db.execute(
                delete(KnowledgeMetadata).where(KnowledgeMetadata.id == metadata_id)
            )
            await db.commit()
        except Exception:
            await db.rollback()
            raise

    @staticmethod
    async def get_builtin_fields_async(
        db: AsyncSession,
        knowledge_id: uuid.UUID,
    ) -> dict:
        knowledge = await db.get(Knowledge, knowledge_id)
        return {
            "enabled": bool(
                knowledge and knowledge.builtin_metadata_enabled == 1
            ),
            "fields": BuiltinFieldResolver.get_all(),
        }

    @staticmethod
    async def set_builtin_metadata_enabled_async(
        db: AsyncSession,
        knowledge_id: uuid.UUID,
        enabled: bool,
    ) -> bool:
        knowledge = await db.get(Knowledge, knowledge_id)
        if knowledge is None:
            raise KnowledgeError.from_code(
                "KB_RESOURCE_NOT_FOUND",
                "Knowledge resource not found",
            )
        knowledge.builtin_metadata_enabled = 1 if enabled else 0
        await db.commit()
        await db.refresh(knowledge)
        return enabled
