"""Knowledge metadata behavior copied from the legacy async service."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from ..errors import KnowledgeError
from ..models.owned import (
    Document,
    Knowledge,
    KnowledgeMetadata,
    KnowledgeMetadataBinding,
)
from ..rag.metadata import BuiltinFieldResolver
from ..repositories.knowledge_metadata import KnowledgeMetadataRepository
from ..utils.datetime_utils import (
    as_utc_aware,
    parse_metadata_time_to_utc_naive,
    utcnow_naive,
)


class KnowledgeMetadataService:
    repository = KnowledgeMetadataRepository
    BUILTIN_FIELD_NAMES = {field.name for field in BuiltinFieldResolver.get_all()}

    @staticmethod
    async def get_metadata_defs_for_filtering_async(
        db: AsyncSession,
        knowledge_id: uuid.UUID,
    ) -> dict[str, dict[str, Any]]:
        response = await KnowledgeMetadataService.list_metadata_fields_for_knowledge_ids_async(
            db,
            [knowledge_id],
            include_builtin_when_disabled=False,
            preserve_single_ids=True,
            include_counts=False,
        )
        definitions: dict[str, dict[str, Any]] = {
            item["name"]: {
                "id": item.get("id"),
                "name": item["name"],
                "type": item["type"],
                "is_builtin": False,
            }
            for item in response["custom"]
        }
        if response["builtin_enabled"]:
            definitions.update(
                {
                    field.name: {
                        "name": field.name,
                        "type": field.type,
                        "is_builtin": True,
                    }
                    for field in response["builtin_fields"]
                }
            )
        return definitions

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

    @staticmethod
    async def batch_update_document_metadata_async(
        db: AsyncSession,
        items: list[dict[str, Any]],
        tenant_id: uuid.UUID,
        created_by: uuid.UUID,
    ) -> dict[str, Any]:
        if not items:
            return {"success_count": 0, "failed_items": []}

        document_ids = [item["document_id"] for item in items]
        result = await db.execute(select(Document).where(Document.id.in_(document_ids)))
        documents = list(result.scalars().all())
        document_by_id = {document.id: document for document in documents}
        if len(document_by_id) != len(set(document_ids)):
            missing = set(document_ids) - set(document_by_id)
            raise KnowledgeError.from_code(
                "KB_RESOURCE_NOT_FOUND",
                f"Document resource not found: {next(iter(missing))}",
            )
        knowledge_ids = {document.kb_id for document in documents}
        if len(knowledge_ids) != 1:
            raise KnowledgeError.from_code(
                "KB_VALIDATION_ERROR",
                "批量更新的文档必须属于同一知识库",
            )

        knowledge_id = next(iter(knowledge_ids))
        custom_fields = await KnowledgeMetadataService.repository.get_by_knowledge_id_async(
            db,
            knowledge_id,
        )
        field_defs = {field.name: field for field in custom_fields}
        failed_items: list[dict[str, str]] = []
        for item in items:
            document_id = item["document_id"]
            for field_name, value in item["metadata"].items():
                field_def = field_defs.get(field_name)
                if field_def is None:
                    failed_items.append(
                        {
                            "document_id": str(document_id),
                            "error": f"字段 '{field_name}' 未在知识库中定义",
                        }
                    )
                elif not KnowledgeMetadataService._validate_value_type(
                    field_def.type,
                    value,
                ):
                    failed_items.append(
                        {
                            "document_id": str(document_id),
                            "error": (
                                f"字段 '{field_name}' 的值类型不匹配，"
                                f"期望 {field_def.type}"
                            ),
                        }
                    )
        if failed_items:
            return {"success_count": 0, "failed_items": failed_items}

        try:
            for item in items:
                document = document_by_id[item["document_id"]]
                metadata = item["metadata"]
                normalized = KnowledgeMetadataService._normalize_metadata_for_storage(
                    metadata,
                    field_defs,
                )
                document.meta_data = dict(document.meta_data or {})
                document.meta_data.update(normalized)
                flag_modified(document, "meta_data")
                document.updated_at = utcnow_naive()
                for field_name in metadata:
                    field_def = field_defs[field_name]
                    exists = await KnowledgeMetadataService.repository.binding_exists_async(
                        db,
                        knowledge_id,
                        field_def.id,
                        document.id,
                    )
                    if not exists:
                        db.add(
                            KnowledgeMetadataBinding(
                                tenant_id=tenant_id,
                                knowledge_id=knowledge_id,
                                metadata_id=field_def.id,
                                document_id=document.id,
                                created_by=created_by,
                            )
                        )
            await db.commit()
        except Exception:
            await db.rollback()
            raise
        return {"success_count": len(items), "failed_items": []}

    @staticmethod
    async def update_document_metadata_async(
        db: AsyncSession,
        document_id: uuid.UUID,
        metadata: dict[str, Any],
        tenant_id: uuid.UUID,
        created_by: uuid.UUID,
    ) -> dict[str, Any]:
        document = await db.get(Document, document_id)
        if document is None:
            raise KnowledgeError.from_code(
                "KB_RESOURCE_NOT_FOUND",
                "Document resource not found",
            )
        custom_fields = await KnowledgeMetadataService.repository.get_by_knowledge_id_async(
            db,
            document.kb_id,
        )
        field_defs = {field.name: field for field in custom_fields}
        for field_name, value in metadata.items():
            field_def = field_defs.get(field_name)
            if field_def is None:
                raise KnowledgeError.from_code(
                    "KB_VALIDATION_ERROR",
                    f"Metadata field is not defined: {field_name}",
                )
            if not KnowledgeMetadataService._validate_value_type(field_def.type, value):
                raise KnowledgeError.from_code(
                    "KB_METADATA_TYPE_MISMATCH",
                    f"Metadata value type does not match: {field_name}",
                )

        normalized = KnowledgeMetadataService._normalize_metadata_for_storage(
            metadata,
            field_defs,
        )
        document.meta_data = dict(document.meta_data or {})
        document.meta_data.update(normalized)
        flag_modified(document, "meta_data")
        document.updated_at = utcnow_naive()
        for field_name in metadata:
            field_def = field_defs[field_name]
            exists = await KnowledgeMetadataService.repository.binding_exists_async(
                db,
                document.kb_id,
                field_def.id,
                document.id,
            )
            if not exists:
                db.add(
                    KnowledgeMetadataBinding(
                        tenant_id=tenant_id,
                        knowledge_id=document.kb_id,
                        metadata_id=field_def.id,
                        document_id=document.id,
                        created_by=created_by,
                    )
                )
        try:
            await db.commit()
            await db.refresh(document)
        except Exception:
            await db.rollback()
            raise
        return await KnowledgeMetadataService.get_document_metadata_async(db, document_id)

    @staticmethod
    async def get_document_metadata_async(
        db: AsyncSession,
        document_id: uuid.UUID,
    ) -> dict[str, Any]:
        document = await db.get(Document, document_id)
        if document is None:
            raise KnowledgeError.from_code(
                "KB_RESOURCE_NOT_FOUND",
                "Document resource not found",
            )
        bindings = (
            await KnowledgeMetadataService.repository.get_bindings_by_document_id_async(
                db,
                document_id,
            )
        )
        custom_fields = await KnowledgeMetadataService.repository.get_by_knowledge_id_async(
            db,
            document.kb_id,
        )
        fields_by_id = {field.id: field for field in custom_fields}
        fields_by_name = {field.name: field for field in custom_fields}
        metadata = KnowledgeMetadataService._serialize_metadata_for_response(
            document.meta_data or {},
            fields_by_name,
        )
        fields = []
        for binding in bindings:
            field = fields_by_id.get(binding.metadata_id)
            if field is not None:
                fields.append(
                    {
                        "field_id": str(field.id),
                        "name": field.name,
                        "type": field.type,
                        "value": metadata.get(field.name),
                    }
                )
        return {
            "document_id": str(document_id),
            "metadata": metadata,
            "fields": fields,
        }

    @staticmethod
    async def delete_document_metadata_async(
        db: AsyncSession,
        document_id: uuid.UUID,
        field_names: list[str] | None = None,
    ) -> dict[str, Any]:
        document = await db.get(Document, document_id)
        if document is None:
            raise KnowledgeError.from_code(
                "KB_RESOURCE_NOT_FOUND",
                "Document resource not found",
            )
        document.meta_data = dict(document.meta_data or {})
        deleted_fields: list[str] = []
        if not field_names:
            deleted_fields = list(document.meta_data)
            document.meta_data = {}
            flag_modified(document, "meta_data")
            await db.execute(
                delete(KnowledgeMetadataBinding).where(
                    KnowledgeMetadataBinding.document_id == document_id
                )
            )
        else:
            custom_fields = (
                await KnowledgeMetadataService.repository.get_by_knowledge_id_async(
                    db,
                    document.kb_id,
                )
            )
            field_defs = {field.name: field for field in custom_fields}
            for field_name in field_names:
                field_def = field_defs.get(field_name)
                if field_name in document.meta_data:
                    del document.meta_data[field_name]
                    deleted_fields.append(field_name)
                if field_def is not None:
                    await db.execute(
                        delete(KnowledgeMetadataBinding).where(
                            KnowledgeMetadataBinding.document_id == document_id,
                            KnowledgeMetadataBinding.metadata_id == field_def.id,
                        )
                    )
            if deleted_fields:
                flag_modified(document, "meta_data")
        try:
            await db.commit()
            await db.refresh(document)
        except Exception:
            await db.rollback()
            raise
        return {
            "document_id": str(document_id),
            "deleted_fields": deleted_fields,
        }

    @staticmethod
    def _validate_value_type(field_type: str, value: Any) -> bool:
        if value is None:
            return True
        if field_type == "string":
            return isinstance(value, str)
        if field_type == "number":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if field_type == "time":
            try:
                return parse_metadata_time_to_utc_naive(value) is not None
            except (TypeError, ValueError):
                return False
        return False

    @staticmethod
    def _normalize_metadata_for_storage(
        metadata: dict[str, Any],
        field_defs: dict[str, KnowledgeMetadata],
    ) -> dict[str, Any]:
        normalized: dict[str, Any] = {}
        for field_name, value in metadata.items():
            if value is None or field_defs[field_name].type != "time":
                normalized[field_name] = value
                continue
            parsed = parse_metadata_time_to_utc_naive(value)
            normalized[field_name] = parsed.isoformat(sep=" ") if parsed else None
        return normalized

    @staticmethod
    def _serialize_metadata_for_response(
        metadata: dict[str, Any],
        field_defs: dict[str, KnowledgeMetadata],
    ) -> dict[str, Any]:
        serialized: dict[str, Any] = {}
        for field_name, value in metadata.items():
            field_def = field_defs.get(field_name)
            if value is None or field_def is None or field_def.type != "time":
                serialized[field_name] = value
                continue
            try:
                parsed = parse_metadata_time_to_utc_naive(value)
            except (TypeError, ValueError):
                serialized[field_name] = value
                continue
            aware = as_utc_aware(parsed)
            serialized[field_name] = aware.isoformat() if aware else value
        return serialized
