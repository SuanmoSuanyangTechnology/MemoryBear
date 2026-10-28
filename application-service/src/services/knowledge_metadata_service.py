import logging

import uuid
from typing import Any
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from ..utils.datetime_utils import (
    parse_metadata_time_to_utc_naive,
    to_utc_offset_string,
)
from ..errors import ResourceNotFoundException
from ..rag.metadata.builtin_resolver import BuiltinFieldResolver
from ..models.knowledge_metadata_model import KnowledgeMetadata
from ..models.document_model import Document
from ..repositories.knowledge_metadata_repository import KnowledgeMetadataRepository

api_logger = logging.getLogger(__name__)


class KnowledgeMetadataService:
    """Knowledge base metadata service."""

    @staticmethod
    def list_metadata_fields(db: Session, knowledge_id: uuid.UUID) -> dict:
        """
        List all metadata fields of a knowledge base (custom + builtin)
        Returns: {"custom": [...], "builtin_enabled": bool, "builtin_fields": [...]}
        """
        return KnowledgeMetadataService.list_metadata_fields_for_knowledge_ids(
            db=db,
            knowledge_ids=[knowledge_id],
            include_builtin_when_disabled=True,
            preserve_single_ids=True,
        )

    @staticmethod
    async def list_metadata_fields_async(db: AsyncSession, knowledge_id: uuid.UUID) -> dict:
        """Async version of list_metadata_fields."""
        return await KnowledgeMetadataService.list_metadata_fields_for_knowledge_ids_async(
            db=db,
            knowledge_ids=[knowledge_id],
            include_builtin_when_disabled=True,
            preserve_single_ids=True,
        )

    @staticmethod
    def list_metadata_fields_for_knowledge_ids(
        db: Session,
        knowledge_ids: list[uuid.UUID],
        *,
        include_builtin_when_disabled: bool = False,
        preserve_single_ids: bool = False,
        include_counts: bool = True,
    ) -> dict:
        """List common metadata fields across knowledge bases."""
        from ..models.knowledge_model import Knowledge

        unique_knowledge_ids = list(dict.fromkeys(knowledge_ids))
        if not unique_knowledge_ids:
            return {
                "custom": [],
                "builtin_enabled": False,
                "builtin_fields": [],
            }

        custom_fields = KnowledgeMetadataRepository.get_by_knowledge_ids(db, unique_knowledge_ids)
        fields_by_kb = {knowledge_id: [] for knowledge_id in unique_knowledge_ids}
        for field in custom_fields:
            fields_by_kb.setdefault(field.knowledge_id, []).append(field)

        counts_by_metadata_id = {}
        if include_counts:
            metadata_ids = [field.id for field in custom_fields]
            counts_by_metadata_id = KnowledgeMetadataRepository.count_active_bindings_by_metadata_ids(
                db,
                metadata_ids,
            )

        knowledge_rows = (
            db.query(Knowledge.id, Knowledge.builtin_metadata_enabled)
            .filter(Knowledge.id.in_(unique_knowledge_ids))
            .all()
        )
        builtin_enabled_by_kb = {
            row[0]: row[1] == 1
            for row in knowledge_rows
        }
        for knowledge_id in unique_knowledge_ids:
            builtin_enabled_by_kb.setdefault(knowledge_id, False)

        return KnowledgeMetadataService._build_common_metadata_fields_response(
            fields_by_kb=fields_by_kb,
            builtin_enabled_by_kb=builtin_enabled_by_kb,
            counts_by_metadata_id=counts_by_metadata_id,
            include_builtin_when_disabled=include_builtin_when_disabled,
            preserve_single_ids=preserve_single_ids,
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
        """Async version of list_metadata_fields_for_knowledge_ids."""
        from ..models.knowledge_model import Knowledge

        unique_knowledge_ids = list(dict.fromkeys(knowledge_ids))
        if not unique_knowledge_ids:
            return {
                "custom": [],
                "builtin_enabled": False,
                "builtin_fields": [],
            }

        custom_fields = await KnowledgeMetadataRepository.get_by_knowledge_ids_async(db, unique_knowledge_ids)
        fields_by_kb = {knowledge_id: [] for knowledge_id in unique_knowledge_ids}
        for field in custom_fields:
            fields_by_kb.setdefault(field.knowledge_id, []).append(field)

        counts_by_metadata_id = {}
        if include_counts:
            metadata_ids = [field.id for field in custom_fields]
            counts_by_metadata_id = await KnowledgeMetadataRepository.count_active_bindings_by_metadata_ids_async(
                db,
                metadata_ids,
            )

        result = await db.execute(
            select(Knowledge.id, Knowledge.builtin_metadata_enabled)
            .where(Knowledge.id.in_(unique_knowledge_ids))
        )
        builtin_enabled_by_kb = {
            row[0]: row[1] == 1
            for row in result.all()
        }
        for knowledge_id in unique_knowledge_ids:
            builtin_enabled_by_kb.setdefault(knowledge_id, False)

        return KnowledgeMetadataService._build_common_metadata_fields_response(
            fields_by_kb=fields_by_kb,
            builtin_enabled_by_kb=builtin_enabled_by_kb,
            counts_by_metadata_id=counts_by_metadata_id,
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
        knowledge_ids = list(fields_by_kb.keys())
        common_keys: set[tuple[str, str]] | None = None
        field_lookup_by_kb: dict[uuid.UUID, dict[tuple[str, str], KnowledgeMetadata]] = {}

        for knowledge_id, fields in fields_by_kb.items():
            lookup = {(field.name, field.type): field for field in fields}
            field_lookup_by_kb[knowledge_id] = lookup
            keys = set(lookup.keys())
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
            fields = [
                field_lookup_by_kb[knowledge_id][key]
                for knowledge_id in knowledge_ids
            ]
            count = sum(counts_by_metadata_id.get(field.id, 0) for field in fields)
            first_field = fields[0]
            custom_field = {
                "id": first_field.id if single_kb and preserve_single_ids else None,
                "type": first_field.type,
                "name": first_field.name,
                "is_builtin": False,
                "count": count,
            }
            if single_kb and preserve_single_ids:
                custom_field["created_at"] = first_field.created_at
                custom_field["updated_at"] = first_field.updated_at
            custom_fields.append(custom_field)

        builtin_enabled = all(
            builtin_enabled_by_kb.get(knowledge_id, False)
            for knowledge_id in knowledge_ids
        ) if knowledge_ids else False

        return {
            "custom": custom_fields,
            "builtin_enabled": builtin_enabled,
            "builtin_fields": BuiltinFieldResolver.get_all()
            if builtin_enabled or include_builtin_when_disabled
            else [],
        }

    @staticmethod
    def get_builtin_fields(db: Session, knowledge_id: uuid.UUID) -> dict:
        """Get the builtin metadata field list and the enabled flag."""
        from ..models.knowledge_model import Knowledge

        knowledge = db.query(Knowledge).filter(Knowledge.id == knowledge_id).first()
        enabled = knowledge.builtin_metadata_enabled == 1 if knowledge else False

        return {
            "enabled": enabled,
            "fields": BuiltinFieldResolver.get_all(),
        }

    @staticmethod
    async def get_builtin_fields_async(db: AsyncSession, knowledge_id: uuid.UUID) -> dict:
        """Async version of get_builtin_fields."""
        from ..models.knowledge_model import Knowledge

        knowledge = await db.get(Knowledge, knowledge_id)
        enabled = knowledge.builtin_metadata_enabled == 1 if knowledge else False

        return {
            "enabled": enabled,
            "fields": BuiltinFieldResolver.get_all(),
        }

    @staticmethod
    def get_document_metadata(
        db: Session,
        document_id: uuid.UUID,
    ) -> dict:
        """
        Get metadata of a single document
        Returns: {"document_id": str, "metadata": {field_name: value}, "bindings": [...]}
        """
        doc = db.query(Document).filter(Document.id == document_id).first()
        if not doc:
            raise ResourceNotFoundException("文档", str(document_id))

        # Load binding records
        bindings = KnowledgeMetadataRepository.get_bindings_by_document_id(db, document_id)
        binding_fields = {b.metadata_id: b for b in bindings}

        # Load field definitions
        custom_fields = KnowledgeMetadataRepository.get_by_knowledge_id(db, doc.kb_id)
        field_map = {f.id: f for f in custom_fields}
        field_defs_by_name = {f.name: f for f in custom_fields}
        metadata = KnowledgeMetadataService._serialize_metadata_for_response(
            doc.meta_data or {},
            field_defs_by_name,
        )

        result = {
            "document_id": str(document_id),
            "metadata": metadata,
            "fields": [],
        }

        for metadata_id, binding in binding_fields.items():
            field_def = field_map.get(metadata_id)
            if field_def:
                result["fields"].append({
                    "field_id": str(field_def.id),
                    "name": field_def.name,
                    "type": field_def.type,
                    "value": metadata.get(field_def.name),
                })

        return result

    @staticmethod
    async def get_document_metadata_async(
        db: AsyncSession,
        document_id: uuid.UUID,
    ) -> dict:
        """Async version of get_document_metadata."""
        doc = await db.get(Document, document_id)
        if not doc:
            raise ResourceNotFoundException("文档", str(document_id))

        bindings = await KnowledgeMetadataRepository.get_bindings_by_document_id_async(db, document_id)
        binding_fields = {b.metadata_id: b for b in bindings}

        custom_fields = await KnowledgeMetadataRepository.get_by_knowledge_id_async(db, doc.kb_id)
        field_map = {f.id: f for f in custom_fields}
        field_defs_by_name = {f.name: f for f in custom_fields}
        metadata = KnowledgeMetadataService._serialize_metadata_for_response(
            doc.meta_data or {},
            field_defs_by_name,
        )

        result = {
            "document_id": str(document_id),
            "metadata": metadata,
            "fields": [],
        }

        for metadata_id, binding in binding_fields.items():
            field_def = field_map.get(metadata_id)
            if field_def:
                result["fields"].append({
                    "field_id": str(field_def.id),
                    "name": field_def.name,
                    "type": field_def.type,
                    "value": metadata.get(field_def.name),
                })

        return result

    @staticmethod
    def _serialize_metadata_value_for_response(field_type: str, value: Any) -> Any:
        """Format metadata values for API responses without changing storage."""
        if value is None or field_type != "time":
            return value
        try:
            dt = parse_metadata_time_to_utc_naive(value)
        except (TypeError, ValueError):
            api_logger.warning(
                "Skipped invalid document time metadata response formatting: "
                f"raw_value={value!r}, raw_type={type(value).__name__}"
            )
            return value
        if dt is None:
            return value
        return to_utc_offset_string(dt)

    @staticmethod
    def _serialize_metadata_for_response(
        metadata: dict[str, Any],
        field_defs: dict[str, Any],
    ) -> dict[str, Any]:
        """Format typed metadata values for API responses."""
        serialized = {}
        for field_name, value in metadata.items():
            field_def = field_defs.get(field_name)
            if not field_def:
                serialized[field_name] = value
                continue
            serialized[field_name] = KnowledgeMetadataService._serialize_metadata_value_for_response(
                field_def.type,
                value,
            )
        return serialized

    @staticmethod
    def get_metadata_defs_for_filtering(
        db: Session,
        knowledge_id: uuid.UUID,
    ) -> dict[str, dict]:
        """
        Get the field definition mapping used for filtering
        Returns: {field_name: {"id": uuid, "type": "string", "is_builtin": False}}
        """
        from ..models.knowledge_model import Knowledge

        result = {}

        # Custom fields
        custom_fields = KnowledgeMetadataRepository.get_by_knowledge_id(db, knowledge_id)
        for f in custom_fields:
            result[f.name] = {"id": f.id, "type": f.type, "is_builtin": False}

        # Builtin fields (when enabled)
        knowledge = db.query(Knowledge).filter(Knowledge.id == knowledge_id).first()
        if knowledge and knowledge.builtin_metadata_enabled == 1:
            for bf in BuiltinFieldResolver.get_all():
                result[bf.name] = {"type": bf.type, "is_builtin": True}

        return result

    @staticmethod
    async def get_metadata_defs_for_filtering_async(
        db: AsyncSession,
        knowledge_id: uuid.UUID,
    ) -> dict[str, dict]:
        """Async version of get_metadata_defs_for_filtering."""
        from ..models.knowledge_model import Knowledge

        result = {}

        custom_fields = await KnowledgeMetadataRepository.get_by_knowledge_id_async(db, knowledge_id)
        for f in custom_fields:
            result[f.name] = {"id": f.id, "type": f.type, "is_builtin": False}

        knowledge = await db.get(Knowledge, knowledge_id)
        if knowledge and knowledge.builtin_metadata_enabled == 1:
            for bf in BuiltinFieldResolver.get_all():
                result[bf.name] = {"type": bf.type, "is_builtin": True}

        return result
