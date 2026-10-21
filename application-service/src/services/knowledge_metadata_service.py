import logging

import uuid
from typing import Any
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from ..utils.datetime_utils import (
    parse_metadata_time_to_utc_naive,
    to_utc_offset_string,
    utcnow_naive,
)
from ..errors import (
    BusinessException,
    ResourceNotFoundException,
    DuplicateResourceException,
    ValidationException,
)
from ..errors import BizCode
from ..rag.metadata.builtin_resolver import BuiltinFieldResolver
from ..models.knowledge_metadata_model import KnowledgeMetadata, KnowledgeMetadataBinding
from ..models.document_model import Document
from ..repositories.knowledge_metadata_repository import KnowledgeMetadataRepository

api_logger = logging.getLogger(__name__)


class KnowledgeMetadataService:
    """Knowledge base metadata service."""

    BUILTIN_FIELD_NAMES = {f.name for f in BuiltinFieldResolver.get_all()}

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
    def create_metadata_field(
        db: Session,
        knowledge_id: uuid.UUID,
        name: str,
        field_type: str,
        tenant_id: uuid.UUID,
        created_by: uuid.UUID,
    ) -> KnowledgeMetadata:
        """Create a custom metadata field."""
        # Ensure the name does not collide with a builtin field
        if name in KnowledgeMetadataService.BUILTIN_FIELD_NAMES:
            raise ValidationException(
                f"字段名 '{name}' 与内置字段冲突",
                field="name",
            )

        # Ensure uniqueness within the knowledge base
        existing = KnowledgeMetadataRepository.get_by_name(db, knowledge_id, name)
        if existing:
            raise DuplicateResourceException(f"字段 '{name}' 已存在")

        metadata_field = KnowledgeMetadata(
            tenant_id=tenant_id,
            knowledge_id=knowledge_id,
            name=name,
            type=field_type,
            created_by=created_by,
            updated_by=created_by,
        )
        return KnowledgeMetadataRepository.create(db, metadata_field)

    @staticmethod
    async def create_metadata_field_async(
        db: AsyncSession,
        knowledge_id: uuid.UUID,
        name: str,
        field_type: str,
        tenant_id: uuid.UUID,
        created_by: uuid.UUID,
    ) -> KnowledgeMetadata:
        """Async version of create_metadata_field."""
        if name in KnowledgeMetadataService.BUILTIN_FIELD_NAMES:
            raise ValidationException(
                f"字段名 '{name}' 与内置字段冲突",
                field="name",
            )

        existing = await KnowledgeMetadataRepository.get_by_name_async(db, knowledge_id, name)
        if existing:
            raise DuplicateResourceException(f"字段 '{name}' 已存在")

        metadata_field = KnowledgeMetadata(
            tenant_id=tenant_id,
            knowledge_id=knowledge_id,
            name=name,
            type=field_type,
            created_by=created_by,
            updated_by=created_by,
        )
        return await KnowledgeMetadataRepository.create_async(db, metadata_field)

    @staticmethod
    def update_metadata_field(
        db: Session,
        metadata_id: uuid.UUID,
        knowledge_id: uuid.UUID,
        name: str | None,
        updated_by: uuid.UUID,
    ) -> KnowledgeMetadata:
        """Update a custom metadata field (only name can be changed)."""
        field = KnowledgeMetadataRepository.get_by_id(db, metadata_id)
        if not field or field.knowledge_id != knowledge_id:
            raise ResourceNotFoundException("元数据字段", str(metadata_id))

        update_data = {"updated_by": updated_by}
        if name and name != field.name:
            if name in KnowledgeMetadataService.BUILTIN_FIELD_NAMES:
                raise ValidationException(
                    f"字段名 '{name}' 与内置字段冲突",
                    field="name",
                )
            existing = KnowledgeMetadataRepository.get_by_name(db, knowledge_id, name)
            if existing and existing.id != metadata_id:
                raise DuplicateResourceException(f"字段 '{name}' 已存在")
            update_data["name"] = name

        KnowledgeMetadataRepository.update(db, metadata_id, update_data)
        db.refresh(field)
        return field

    @staticmethod
    async def update_metadata_field_async(
        db: AsyncSession,
        metadata_id: uuid.UUID,
        knowledge_id: uuid.UUID,
        name: str | None,
        updated_by: uuid.UUID,
    ) -> KnowledgeMetadata:
        """Async version of update_metadata_field."""
        field = await KnowledgeMetadataRepository.get_by_id_async(db, metadata_id)
        if not field or field.knowledge_id != knowledge_id:
            raise ResourceNotFoundException("元数据字段", str(metadata_id))

        update_data = {"updated_by": updated_by}
        if name and name != field.name:
            if name in KnowledgeMetadataService.BUILTIN_FIELD_NAMES:
                raise ValidationException(
                    f"字段名 '{name}' 与内置字段冲突",
                    field="name",
                )
            existing = await KnowledgeMetadataRepository.get_by_name_async(db, knowledge_id, name)
            if existing and existing.id != metadata_id:
                raise DuplicateResourceException(f"字段 '{name}' 已存在")
            update_data["name"] = name

        await KnowledgeMetadataRepository.update_async(db, metadata_id, update_data)
        await db.refresh(field)
        return field

    @staticmethod
    def delete_metadata_field(
        db: Session,
        metadata_id: uuid.UUID,
        knowledge_id: uuid.UUID,
    ) -> None:
        """Delete a custom metadata field; also cleans up bindings and document metadata JSON."""
        field = KnowledgeMetadataRepository.get_by_id(db, metadata_id)
        if not field or field.knowledge_id != knowledge_id:
            raise ResourceNotFoundException("元数据字段", str(metadata_id))

        field_name = field.name

        # 1. Delete binding records
        KnowledgeMetadataRepository.delete_bindings_by_metadata_id(db, metadata_id)

        # 2. Remove this field from every document metadata JSON
        db.query(Document).filter(
            Document.kb_id == knowledge_id
        ).update(
            {Document.meta_data: Document.meta_data.op("-")(field_name)},
            synchronize_session=False,
        )

        # 3. Delete the field definition
        KnowledgeMetadataRepository.delete(db, metadata_id)

        api_logger.info(f"Deleted metadata field '{field_name}' and cleaned up document metadata")

    @staticmethod
    async def delete_metadata_field_async(
        db: AsyncSession,
        metadata_id: uuid.UUID,
        knowledge_id: uuid.UUID,
    ) -> None:
        """Async version of delete_metadata_field."""
        field = await KnowledgeMetadataRepository.get_by_id_async(db, metadata_id)
        if not field or field.knowledge_id != knowledge_id:
            raise ResourceNotFoundException("元数据字段", str(metadata_id))

        field_name = field.name
        try:
            await db.execute(
                delete(KnowledgeMetadataBinding).where(
                    KnowledgeMetadataBinding.metadata_id == metadata_id
                )
            )
            await db.execute(
                update(Document)
                .where(Document.kb_id == knowledge_id)
                .values({Document.meta_data: Document.meta_data.op("-")(field_name)})
                .execution_options(synchronize_session=False)
            )
            await db.execute(
                delete(KnowledgeMetadata).where(KnowledgeMetadata.id == metadata_id)
            )
            await db.commit()
        except Exception:
            await db.rollback()
            raise

        api_logger.info(f"Deleted metadata field '{field_name}' and cleaned up document metadata")

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
    def set_builtin_metadata_enabled(
        db: Session,
        knowledge_id: uuid.UUID,
        enabled: bool,
    ) -> bool:
        """Set the builtin metadata flag."""
        from ..models.knowledge_model import Knowledge

        knowledge = db.query(Knowledge).filter(Knowledge.id == knowledge_id).first()
        if not knowledge:
            raise ResourceNotFoundException("知识库", str(knowledge_id))

        knowledge.builtin_metadata_enabled = 1 if enabled else 0
        db.commit()
        db.refresh(knowledge)

        return enabled

    @staticmethod
    async def set_builtin_metadata_enabled_async(
        db: AsyncSession,
        knowledge_id: uuid.UUID,
        enabled: bool,
    ) -> bool:
        """Async version of set_builtin_metadata_enabled."""
        from ..models.knowledge_model import Knowledge

        knowledge = await db.get(Knowledge, knowledge_id)
        if not knowledge:
            raise ResourceNotFoundException("知识库", str(knowledge_id))

        knowledge.builtin_metadata_enabled = 1 if enabled else 0
        await db.commit()
        await db.refresh(knowledge)

        return enabled

    @staticmethod
    def batch_update_document_metadata(
        db: Session,
        items: list[dict],
        tenant_id: uuid.UUID,
        created_by: uuid.UUID,
    ) -> dict:
        """
        Batch update document metadata
        Args:
            items: [{"document_id": uuid, "metadata": {field_name: value}}]
        Returns:
            {"success_count": int, "failed_items": [...]}
        """
        if not items:
            return {"success_count": 0, "failed_items": []}

        # Collect all document_ids
        document_ids = [item["document_id"] for item in items]

        # Load the documents and check they belong to the same knowledge base
        documents = db.query(Document).filter(Document.id.in_(document_ids)).all()
        doc_map = {doc.id: doc for doc in documents}

        if len(documents) != len(document_ids):
            missing = set(document_ids) - set(doc_map.keys())
            raise ResourceNotFoundException("文档", str(list(missing)[0]))

        kb_ids = {doc.kb_id for doc in documents}
        if len(kb_ids) != 1:
            raise BusinessException(
                "批量更新的文档必须属于同一知识库",
                code=BizCode.METADATA_CROSS_KB_BATCH,
            )

        knowledge_id = list(kb_ids)[0]

        # Load the metadata field definitions of this knowledge base
        custom_fields = KnowledgeMetadataRepository.get_by_knowledge_id(db, knowledge_id)
        field_defs = {f.name: f for f in custom_fields}

        # Step 1: validate everything (no DB writes)
        failed_items = []
        for item in items:
            doc_id = item["document_id"]
            metadata = item["metadata"]
            doc = doc_map.get(doc_id)

            if not doc:
                failed_items.append({"document_id": str(doc_id), "error": "文档不存在"})
                continue

            for field_name, value in metadata.items():
                field_def = field_defs.get(field_name)
                if not field_def:
                    failed_items.append({
                        "document_id": str(doc_id),
                        "error": f"字段 '{field_name}' 未在知识库中定义",
                    })
                elif not KnowledgeMetadataService._validate_value_type(field_def.type, value):
                    failed_items.append({
                        "document_id": str(doc_id),
                        "error": f"字段 '{field_name}' 的值类型不匹配，期望 {field_def.type}",
                    })

        if failed_items:
            return {"success_count": 0, "failed_items": failed_items}

        # Step 2: write only after validation fully passes
        success_count = 0
        try:
            for item in items:
                doc_id = item["document_id"]
                metadata = item["metadata"]
                doc = doc_map[doc_id]
                normalized_metadata = KnowledgeMetadataService._normalize_metadata_for_storage(
                    metadata,
                    field_defs,
                )
                doc.meta_data = doc.meta_data or {}
                doc.meta_data.update(normalized_metadata)
                flag_modified(doc, "meta_data")
                doc.updated_at = utcnow_naive()

                for field_name in metadata.keys():
                    field_def = field_defs[field_name]
                    if not KnowledgeMetadataRepository.binding_exists(
                        db, knowledge_id, field_def.id, doc_id
                    ):
                        binding = KnowledgeMetadataBinding(
                            tenant_id=tenant_id,
                            knowledge_id=knowledge_id,
                            metadata_id=field_def.id,
                            document_id=doc_id,
                            created_by=created_by,
                        )
                        db.add(binding)

                success_count += 1

            db.commit()
        except Exception:
            db.rollback()
            raise

        return {"success_count": success_count, "failed_items": []}

    @staticmethod
    async def batch_update_document_metadata_async(
        db: AsyncSession,
        items: list[dict],
        tenant_id: uuid.UUID,
        created_by: uuid.UUID,
    ) -> dict:
        """Async version of batch_update_document_metadata."""
        if not items:
            return {"success_count": 0, "failed_items": []}

        document_ids = [item["document_id"] for item in items]
        result = await db.execute(select(Document).where(Document.id.in_(document_ids)))
        documents = list(result.scalars().all())
        doc_map = {doc.id: doc for doc in documents}

        if len(documents) != len(document_ids):
            missing = set(document_ids) - set(doc_map.keys())
            raise ResourceNotFoundException("文档", str(list(missing)[0]))

        kb_ids = {doc.kb_id for doc in documents}
        if len(kb_ids) != 1:
            raise BusinessException(
                "批量更新的文档必须属于同一知识库",
                code=BizCode.METADATA_CROSS_KB_BATCH,
            )

        knowledge_id = list(kb_ids)[0]
        custom_fields = await KnowledgeMetadataRepository.get_by_knowledge_id_async(db, knowledge_id)
        field_defs = {f.name: f for f in custom_fields}

        failed_items = []
        for item in items:
            doc_id = item["document_id"]
            metadata = item["metadata"]
            doc = doc_map.get(doc_id)

            if not doc:
                failed_items.append({"document_id": str(doc_id), "error": "文档不存在"})
                continue

            for field_name, value in metadata.items():
                field_def = field_defs.get(field_name)
                if not field_def:
                    failed_items.append({
                        "document_id": str(doc_id),
                        "error": f"字段 '{field_name}' 未在知识库中定义",
                    })
                elif not KnowledgeMetadataService._validate_value_type(field_def.type, value):
                    failed_items.append({
                        "document_id": str(doc_id),
                        "error": f"字段 '{field_name}' 的值类型不匹配，期望 {field_def.type}",
                    })

        if failed_items:
            return {"success_count": 0, "failed_items": failed_items}

        success_count = 0
        try:
            for item in items:
                doc_id = item["document_id"]
                metadata = item["metadata"]
                doc = doc_map[doc_id]
                normalized_metadata = KnowledgeMetadataService._normalize_metadata_for_storage(
                    metadata,
                    field_defs,
                )
                doc.meta_data = doc.meta_data or {}
                doc.meta_data.update(normalized_metadata)
                flag_modified(doc, "meta_data")
                doc.updated_at = utcnow_naive()

                for field_name in metadata.keys():
                    field_def = field_defs[field_name]
                    if not await KnowledgeMetadataRepository.binding_exists_async(
                        db, knowledge_id, field_def.id, doc_id
                    ):
                        binding = KnowledgeMetadataBinding(
                            tenant_id=tenant_id,
                            knowledge_id=knowledge_id,
                            metadata_id=field_def.id,
                            document_id=doc_id,
                            created_by=created_by,
                        )
                        db.add(binding)

                success_count += 1

            await db.commit()
        except Exception:
            await db.rollback()
            raise

        return {"success_count": success_count, "failed_items": []}

    @staticmethod
    def update_document_metadata(
        db: Session,
        document_id: uuid.UUID,
        metadata: dict[str, Any],
        tenant_id: uuid.UUID,
        created_by: uuid.UUID,
    ) -> dict:
        """
        Update metadata of a single document
        Args:
            document_id: Document ID
            metadata: {field_name: value}
        Returns:
            Updated document metadata details
        """
        # 1. Load the document
        doc = db.query(Document).filter(Document.id == document_id).first()
        if not doc:
            raise ResourceNotFoundException("文档", str(document_id))

        knowledge_id = doc.kb_id

        # 2. Load the metadata field definitions of this knowledge base
        custom_fields = KnowledgeMetadataRepository.get_by_knowledge_id(db, knowledge_id)
        field_defs = {f.name: f for f in custom_fields}

        # 3. Validate each field
        for field_name, value in metadata.items():
            field_def = field_defs.get(field_name)
            if not field_def:
                raise ValidationException(
                    f"字段 '{field_name}' 未在知识库中定义",
                    field=field_name,
                )

            if not KnowledgeMetadataService._validate_value_type(field_def.type, value):
                raise ValidationException(
                    f"字段 '{field_name}' 的值类型不匹配，期望 {field_def.type}",
                    field=field_name,
                )

        # 4. Update the metadata JSON
        normalized_metadata = KnowledgeMetadataService._normalize_metadata_for_storage(
            metadata,
            field_defs,
        )
        doc.meta_data = doc.meta_data or {}
        doc.meta_data.update(normalized_metadata)
        flag_modified(doc, "meta_data")
        doc.updated_at = utcnow_naive()

        # 5. Create/update binding records
        for field_name in metadata.keys():
            field_def = field_defs[field_name]
            if not KnowledgeMetadataRepository.binding_exists(
                db, knowledge_id, field_def.id, document_id
            ):
                binding = KnowledgeMetadataBinding(
                    tenant_id=tenant_id,
                    knowledge_id=knowledge_id,
                    metadata_id=field_def.id,
                    document_id=document_id,
                    created_by=created_by,
                )
                db.add(binding)

        db.commit()
        db.refresh(doc)
        return KnowledgeMetadataService.get_document_metadata(db, document_id)

    @staticmethod
    async def update_document_metadata_async(
        db: AsyncSession,
        document_id: uuid.UUID,
        metadata: dict[str, Any],
        tenant_id: uuid.UUID,
        created_by: uuid.UUID,
    ) -> dict:
        """Async version of update_document_metadata."""
        doc = await db.get(Document, document_id)
        if not doc:
            raise ResourceNotFoundException("文档", str(document_id))

        knowledge_id = doc.kb_id
        custom_fields = await KnowledgeMetadataRepository.get_by_knowledge_id_async(db, knowledge_id)
        field_defs = {f.name: f for f in custom_fields}

        for field_name, value in metadata.items():
            field_def = field_defs.get(field_name)
            if not field_def:
                raise ValidationException(
                    f"字段 '{field_name}' 未在知识库中定义",
                    field=field_name,
                )

            if not KnowledgeMetadataService._validate_value_type(field_def.type, value):
                raise ValidationException(
                    f"字段 '{field_name}' 的值类型不匹配，期望 {field_def.type}",
                    field=field_name,
                )

        normalized_metadata = KnowledgeMetadataService._normalize_metadata_for_storage(
            metadata,
            field_defs,
        )
        doc.meta_data = doc.meta_data or {}
        doc.meta_data.update(normalized_metadata)
        flag_modified(doc, "meta_data")
        doc.updated_at = utcnow_naive()

        for field_name in metadata.keys():
            field_def = field_defs[field_name]
            if not await KnowledgeMetadataRepository.binding_exists_async(
                db, knowledge_id, field_def.id, document_id
            ):
                binding = KnowledgeMetadataBinding(
                    tenant_id=tenant_id,
                    knowledge_id=knowledge_id,
                    metadata_id=field_def.id,
                    document_id=document_id,
                    created_by=created_by,
                )
                db.add(binding)

        await db.commit()
        await db.refresh(doc)
        return await KnowledgeMetadataService.get_document_metadata_async(db, document_id)

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
    def delete_document_metadata(
        db: Session,
        document_id: uuid.UUID,
        field_names: list[str] | None = None,
    ) -> dict:
        """
        Delete metadata of a single document
        Args:
            document_id: Document ID
            field_names: Field names to delete; None clears everything
        Returns:
            {"document_id": str, "deleted_fields": [str]}
        """
        doc = db.query(Document).filter(Document.id == document_id).first()
        if not doc:
            raise ResourceNotFoundException("文档", str(document_id))

        doc.meta_data = doc.meta_data or {}
        knowledge_id = doc.kb_id
        deleted_fields = []

        if field_names is None or len(field_names) == 0:
            # Clear everything
            deleted_fields = list(doc.meta_data.keys()) if doc.meta_data else []
            doc.meta_data = {}
            flag_modified(doc, "meta_data")
            # Delete all bindings
            KnowledgeMetadataRepository.delete_bindings_by_document_id(db, document_id)
        else:
            # Delete the specified fields
            custom_fields = KnowledgeMetadataRepository.get_by_knowledge_id(db, knowledge_id)
            field_defs = {f.name: f for f in custom_fields}
            skipped_fields = []

            for field_name in field_names:
                field_def = field_defs.get(field_name)
                if field_name not in doc.meta_data and not field_def:
                    skipped_fields.append(field_name)
                    continue

                if field_name in doc.meta_data:
                    del doc.meta_data[field_name]
                    deleted_fields.append(field_name)

                # Delete the corresponding binding
                if field_def:
                    db.query(KnowledgeMetadataBinding).filter(
                        KnowledgeMetadataBinding.document_id == document_id,
                        KnowledgeMetadataBinding.metadata_id == field_def.id,
                    ).delete()

            if deleted_fields:
                flag_modified(doc, "meta_data")
            if skipped_fields:
                api_logger.warning(
                    "Skipped unknown document metadata fields: "
                    f"document_id={document_id}, knowledge_id={knowledge_id}, "
                    f"field_names={skipped_fields}"
                )

        db.commit()
        db.refresh(doc)

        return {
            "document_id": str(document_id),
            "deleted_fields": deleted_fields,
        }

    @staticmethod
    async def delete_document_metadata_async(
        db: AsyncSession,
        document_id: uuid.UUID,
        field_names: list[str] | None = None,
    ) -> dict:
        """Async version of delete_document_metadata."""
        doc = await db.get(Document, document_id)
        if not doc:
            raise ResourceNotFoundException("文档", str(document_id))

        doc.meta_data = doc.meta_data or {}
        knowledge_id = doc.kb_id
        deleted_fields = []

        if field_names is None or len(field_names) == 0:
            deleted_fields = list(doc.meta_data.keys()) if doc.meta_data else []
            doc.meta_data = {}
            flag_modified(doc, "meta_data")
            await db.execute(
                delete(KnowledgeMetadataBinding).where(
                    KnowledgeMetadataBinding.document_id == document_id
                )
            )
        else:
            custom_fields = await KnowledgeMetadataRepository.get_by_knowledge_id_async(db, knowledge_id)
            field_defs = {f.name: f for f in custom_fields}
            skipped_fields = []

            for field_name in field_names:
                field_def = field_defs.get(field_name)
                if field_name not in doc.meta_data and not field_def:
                    skipped_fields.append(field_name)
                    continue

                if field_name in doc.meta_data:
                    del doc.meta_data[field_name]
                    deleted_fields.append(field_name)

                if field_def:
                    await db.execute(
                        delete(KnowledgeMetadataBinding).where(
                            KnowledgeMetadataBinding.document_id == document_id,
                            KnowledgeMetadataBinding.metadata_id == field_def.id,
                        )
                    )

            if deleted_fields:
                flag_modified(doc, "meta_data")
            if skipped_fields:
                api_logger.warning(
                    "Skipped unknown document metadata fields: "
                    f"document_id={document_id}, knowledge_id={knowledge_id}, "
                    f"field_names={skipped_fields}"
                )

        await db.commit()
        await db.refresh(doc)

        return {
            "document_id": str(document_id),
            "deleted_fields": deleted_fields,
        }

    @staticmethod
    def _validate_value_type(field_type: str, value: Any) -> bool:
        """Check whether the value type matches the field definition."""
        if value is None:
            return True
        match field_type:
            case "string":
                return isinstance(value, str)
            case "number":
                return isinstance(value, (int, float)) and not isinstance(value, bool)
            case "time":
                try:
                    return parse_metadata_time_to_utc_naive(value) is not None
                except (TypeError, ValueError):
                    return False
            case _:
                return False

    @staticmethod
    def _normalize_metadata_value_for_storage(field_type: str, value: Any) -> Any:
        """Normalize metadata values before storing them in Document.meta_data."""
        if value is None or field_type != "time":
            return value
        dt = parse_metadata_time_to_utc_naive(value)
        if dt is None:
            return None
        return dt.isoformat(sep=" ")

    @staticmethod
    def _normalize_metadata_for_storage(
        metadata: dict[str, Any],
        field_defs: dict[str, Any],
    ) -> dict[str, Any]:
        """Normalize time metadata to naive UTC strings before JSONB storage."""
        normalized = {}
        for field_name, value in metadata.items():
            field_def = field_defs[field_name]
            normalized[field_name] = KnowledgeMetadataService._normalize_metadata_value_for_storage(
                field_def.type,
                value,
            )
            if field_def.type == "time":
                api_logger.debug(
                    "Normalized document time metadata: "
                    f"field={field_name}, raw_value={value!r}, raw_type={type(value).__name__}, "
                    f"stored_value={normalized[field_name]!r}"
                )
        return normalized

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
