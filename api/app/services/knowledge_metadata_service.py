import uuid
from typing import Any
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from app.core.exceptions import (
    BusinessException,
    ResourceNotFoundException,
    DuplicateResourceException,
    ValidationException,
)
from app.core.error_codes import BizCode
from app.core.rag.metadata.builtin_resolver import BuiltinFieldResolver
from app.models.knowledge_metadata_model import KnowledgeMetadata, KnowledgeMetadataBinding
from app.models.document_model import Document
from app.repositories.knowledge_metadata_repository import KnowledgeMetadataRepository
from app.core.logging_config import get_api_logger

api_logger = get_api_logger()


class KnowledgeMetadataService:
    """知识库元数据 Service"""

    BUILTIN_FIELD_NAMES = {f.name for f in BuiltinFieldResolver.get_all()}

    @staticmethod
    def list_metadata_fields(db: Session, knowledge_id: uuid.UUID) -> dict:
        """
        获取知识库的所有元数据字段（自定义 + 内置）
        Returns: {"custom": [...], "builtin_enabled": bool, "builtin_fields": [...]}
        """
        from app.models.knowledge_model import Knowledge

        custom_fields = KnowledgeMetadataRepository.get_by_knowledge_id(db, knowledge_id)

        knowledge = db.query(Knowledge).filter(Knowledge.id == knowledge_id).first()
        builtin_enabled = knowledge.builtin_metadata_enabled == 1 if knowledge else False

        return {
            "custom": custom_fields,
            "builtin_enabled": builtin_enabled,
            "builtin_fields": BuiltinFieldResolver.get_all() if builtin_enabled else [],
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
        """创建自定义元数据字段"""
        # 校验 name 不与内置字段冲突
        if name in KnowledgeMetadataService.BUILTIN_FIELD_NAMES:
            raise ValidationException(
                f"字段名 '{name}' 与内置字段冲突",
                field="name",
            )

        # 校验知识库内唯一
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
    def update_metadata_field(
        db: Session,
        metadata_id: uuid.UUID,
        knowledge_id: uuid.UUID,
        name: str | None,
        updated_by: uuid.UUID,
    ) -> KnowledgeMetadata:
        """更新自定义元数据字段（仅支持修改 name）"""
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
    def delete_metadata_field(
        db: Session,
        metadata_id: uuid.UUID,
        knowledge_id: uuid.UUID,
    ) -> None:
        """删除自定义元数据字段，同步清理绑定和文档 metadata JSON"""
        field = KnowledgeMetadataRepository.get_by_id(db, metadata_id)
        if not field or field.knowledge_id != knowledge_id:
            raise ResourceNotFoundException("元数据字段", str(metadata_id))

        field_name = field.name

        # 1. 删除绑定记录
        KnowledgeMetadataRepository.delete_bindings_by_metadata_id(db, metadata_id)

        # 2. 清理所有文档 metadata JSON 中的该字段
        db.query(Document).filter(
            Document.kb_id == knowledge_id
        ).update(
            {Document.doc_metadata: Document.doc_metadata.op("-")(field_name)},
            synchronize_session=False,
        )

        # 3. 删除字段定义
        KnowledgeMetadataRepository.delete(db, metadata_id)

        api_logger.info(f"Deleted metadata field '{field_name}' and cleaned up document metadata")

    @staticmethod
    def get_builtin_fields(db: Session, knowledge_id: uuid.UUID) -> dict:
        """获取内置元数据字段列表及开关状态"""
        from app.models.knowledge_model import Knowledge

        knowledge = db.query(Knowledge).filter(Knowledge.id == knowledge_id).first()
        enabled = knowledge.builtin_metadata_enabled == 1 if knowledge else False

        return {
            "enabled": enabled,
            "fields": BuiltinFieldResolver.get_all() if enabled else [],
        }

    @staticmethod
    def set_builtin_metadata_enabled(
        db: Session,
        knowledge_id: uuid.UUID,
        enabled: bool,
    ) -> bool:
        """设置内置元数据开关"""
        from app.models.knowledge_model import Knowledge

        knowledge = db.query(Knowledge).filter(Knowledge.id == knowledge_id).first()
        if not knowledge:
            raise ResourceNotFoundException("知识库", str(knowledge_id))

        knowledge.builtin_metadata_enabled = 1 if enabled else 0
        db.commit()
        db.refresh(knowledge)

        return enabled

    @staticmethod
    def batch_update_document_metadata(
        db: Session,
        items: list[dict],
        tenant_id: uuid.UUID,
        created_by: uuid.UUID,
    ) -> dict:
        """
        批量更新文档元数据
        Args:
            items: [{"document_id": uuid, "metadata": {field_name: value}}]
        Returns:
            {"success_count": int, "failed_items": [...]}
        """
        if not items:
            return {"success_count": 0, "failed_items": []}

        # 提取所有 document_ids
        document_ids = [item["document_id"] for item in items]

        # 查询文档，校验同知识库
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

        # 获取该知识库的元数据字段定义
        custom_fields = KnowledgeMetadataRepository.get_by_knowledge_id(db, knowledge_id)
        field_defs = {f.name: f for f in custom_fields}

        success_count = 0
        failed_items = []

        try:
            for item in items:
                doc_id = item["document_id"]
                metadata = item["metadata"]
                doc = doc_map.get(doc_id)

                if not doc:
                    failed_items.append({"document_id": str(doc_id), "error": "文档不存在"})
                    continue

                # 校验每个字段
                for field_name, value in metadata.items():
                    field_def = field_defs.get(field_name)
                    if not field_def:
                        failed_items.append({
                            "document_id": str(doc_id),
                            "error": f"字段 '{field_name}' 未在知识库中定义",
                        })
                        raise Exception("validation failed")

                    # 校验值类型
                    if not KnowledgeMetadataService._validate_value_type(field_def.type, value):
                        failed_items.append({
                            "document_id": str(doc_id),
                            "error": f"字段 '{field_name}' 的值类型不匹配，期望 {field_def.type}",
                        })
                        raise Exception("validation failed")

                # 更新 metadata JSON
                doc.doc_metadata.update(metadata)
                flag_modified(doc, "doc_metadata")
                doc.updated_at = __import__('datetime').datetime.now()

                # 创建/更新绑定记录
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
            # 如果已经有部分失败的记录，保留
            if not failed_items:
                raise

        return {"success_count": success_count, "failed_items": failed_items}

    @staticmethod
    def update_document_metadata(
        db: Session,
        document_id: uuid.UUID,
        metadata: dict[str, Any],
        tenant_id: uuid.UUID,
        created_by: uuid.UUID,
    ) -> Document:
        """
        更新单个文档的元数据
        Args:
            document_id: 文档ID
            metadata: {field_name: value}
        Returns:
            更新后的 Document
        """
        # 1. 查询文档
        doc = db.query(Document).filter(Document.id == document_id).first()
        if not doc:
            raise ResourceNotFoundException("文档", str(document_id))

        knowledge_id = doc.kb_id

        # 2. 获取该知识库的元数据字段定义
        custom_fields = KnowledgeMetadataRepository.get_by_knowledge_id(db, knowledge_id)
        field_defs = {f.name: f for f in custom_fields}

        # 3. 校验每个字段
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

        # 4. 更新 metadata JSON
        doc.doc_metadata.update(metadata)
        flag_modified(doc, "doc_metadata")
        doc.updated_at = datetime.datetime.now()

        # 5. 创建/更新绑定记录
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
        return doc

    @staticmethod
    def get_document_metadata(
        db: Session,
        document_id: uuid.UUID,
    ) -> dict:
        """
        获取单个文档的元数据
        Returns: {"document_id": str, "metadata": {field_name: value}, "bindings": [...]}
        """
        doc = db.query(Document).filter(Document.id == document_id).first()
        if not doc:
            raise ResourceNotFoundException("文档", str(document_id))

        # 获取绑定记录
        bindings = KnowledgeMetadataRepository.get_bindings_by_document_id(db, document_id)
        binding_fields = {b.metadata_id: b for b in bindings}

        # 获取字段定义
        custom_fields = KnowledgeMetadataRepository.get_by_knowledge_id(db, doc.kb_id)
        field_map = {f.id: f for f in custom_fields}

        result = {
            "document_id": str(document_id),
            "metadata": doc.doc_metadata or {},
            "fields": [],
        }

        for metadata_id, binding in binding_fields.items():
            field_def = field_map.get(metadata_id)
            if field_def:
                result["fields"].append({
                    "field_id": str(field_def.id),
                    "name": field_def.name,
                    "type": field_def.type,
                    "value": doc.doc_metadata.get(field_def.name) if doc.doc_metadata else None,
                })

        return result

    @staticmethod
    def delete_document_metadata(
        db: Session,
        document_id: uuid.UUID,
        field_names: list[str] | None = None,
    ) -> dict:
        """
        删除单个文档的元数据
        Args:
            document_id: 文档ID
            field_names: 要删除的字段名列表，None 表示清空全部
        Returns:
            {"document_id": str, "deleted_fields": [str]}
        """
        doc = db.query(Document).filter(Document.id == document_id).first()
        if not doc:
            raise ResourceNotFoundException("文档", str(document_id))

        knowledge_id = doc.kb_id
        deleted_fields = []

        if field_names is None or len(field_names) == 0:
            # 清空全部
            deleted_fields = list(doc.doc_metadata.keys()) if doc.doc_metadata else []
            doc.doc_metadata = {}
            flag_modified(doc, "doc_metadata")
            # 删除所有绑定
            KnowledgeMetadataRepository.delete_bindings_by_document_id(db, document_id)
        else:
            # 删除指定字段
            custom_fields = KnowledgeMetadataRepository.get_by_knowledge_id(db, knowledge_id)
            field_defs = {f.name: f for f in custom_fields}

            for field_name in field_names:
                if field_name in doc.doc_metadata:
                    del doc.doc_metadata[field_name]
                    deleted_fields.append(field_name)

                # 删除对应绑定
                field_def = field_defs.get(field_name)
                if field_def:
                    db.query(KnowledgeMetadataBinding).filter(
                        KnowledgeMetadataBinding.document_id == document_id,
                        KnowledgeMetadataBinding.metadata_id == field_def.id,
                    ).delete()

            if deleted_fields:
                flag_modified(doc, "doc_metadata")

        db.commit()
        db.refresh(doc)

        return {
            "document_id": str(document_id),
            "deleted_fields": deleted_fields,
        }

    @staticmethod
    def _validate_value_type(field_type: str, value: Any) -> bool:
        """校验值类型是否与字段定义一致"""
        if value is None:
            return True
        match field_type:
            case "string":
                return isinstance(value, str)
            case "number":
                return isinstance(value, (int, float)) and not isinstance(value, bool)
            case "time":
                return isinstance(value, str)
            case _:
                return False

    @staticmethod
    def get_metadata_defs_for_filtering(
        db: Session,
        knowledge_id: uuid.UUID,
    ) -> dict[str, dict]:
        """
        获取用于过滤的字段定义映射
        Returns: {field_name: {"type": "string", "is_builtin": False}}
        """
        from app.models.knowledge_model import Knowledge

        result = {}

        # 自定义字段
        custom_fields = KnowledgeMetadataRepository.get_by_knowledge_id(db, knowledge_id)
        for f in custom_fields:
            result[f.name] = {"type": f.type, "is_builtin": False}

        # 内置字段（如果开启）
        knowledge = db.query(Knowledge).filter(Knowledge.id == knowledge_id).first()
        if knowledge and knowledge.builtin_metadata_enabled == 1:
            for bf in BuiltinFieldResolver.get_all():
                result[bf.name] = {"type": bf.type, "is_builtin": True}

        return result
