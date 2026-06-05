import uuid
from sqlalchemy.orm import Session
from sqlalchemy import and_, delete, update
from app.models.knowledge_metadata_model import KnowledgeMetadata, KnowledgeMetadataBinding
from app.core.logging_config import get_db_logger

db_logger = get_db_logger()


class KnowledgeMetadataRepository:
    """知识库元数据 Repository"""

    @staticmethod
    def create(db: Session, obj: KnowledgeMetadata) -> KnowledgeMetadata:
        db.add(obj)
        db.commit()
        db.refresh(obj)
        return obj

    @staticmethod
    def get_by_id(db: Session, metadata_id: uuid.UUID) -> KnowledgeMetadata | None:
        return db.query(KnowledgeMetadata).filter(KnowledgeMetadata.id == metadata_id).first()

    @staticmethod
    def get_by_knowledge_id(db: Session, knowledge_id: uuid.UUID) -> list[KnowledgeMetadata]:
        return db.query(KnowledgeMetadata).filter(
            KnowledgeMetadata.knowledge_id == knowledge_id
        ).all()

    @staticmethod
    def get_by_name(db: Session, knowledge_id: uuid.UUID, name: str) -> KnowledgeMetadata | None:
        return db.query(KnowledgeMetadata).filter(
            and_(
                KnowledgeMetadata.knowledge_id == knowledge_id,
                KnowledgeMetadata.name == name,
            )
        ).first()

    @staticmethod
    def update(db: Session, metadata_id: uuid.UUID, update_data: dict) -> int:
        result = db.query(KnowledgeMetadata).filter(
            KnowledgeMetadata.id == metadata_id
        ).update(update_data)
        db.commit()
        return result

    @staticmethod
    def delete(db: Session, metadata_id: uuid.UUID) -> int:
        result = db.query(KnowledgeMetadata).filter(
            KnowledgeMetadata.id == metadata_id
        ).delete()
        db.commit()
        return result

    @staticmethod
    def delete_by_knowledge_id(db: Session, knowledge_id: uuid.UUID) -> int:
        result = db.query(KnowledgeMetadata).filter(
            KnowledgeMetadata.knowledge_id == knowledge_id
        ).delete()
        db.commit()
        return result

    # === Binding Operations ===

    @staticmethod
    def create_binding(db: Session, obj: KnowledgeMetadataBinding) -> KnowledgeMetadataBinding:
        db.add(obj)
        db.commit()
        db.refresh(obj)
        return obj

    @staticmethod
    def get_bindings_by_metadata_id(db: Session, metadata_id: uuid.UUID) -> list[KnowledgeMetadataBinding]:
        return db.query(KnowledgeMetadataBinding).filter(
            KnowledgeMetadataBinding.metadata_id == metadata_id
        ).all()

    @staticmethod
    def get_bindings_by_document_id(db: Session, document_id: uuid.UUID) -> list[KnowledgeMetadataBinding]:
        return db.query(KnowledgeMetadataBinding).filter(
            KnowledgeMetadataBinding.document_id == document_id
        ).all()

    @staticmethod
    def delete_bindings_by_metadata_id(db: Session, metadata_id: uuid.UUID) -> int:
        result = db.query(KnowledgeMetadataBinding).filter(
            KnowledgeMetadataBinding.metadata_id == metadata_id
        ).delete()
        db.commit()
        return result

    @staticmethod
    def delete_bindings_by_document_id(db: Session, document_id: uuid.UUID) -> int:
        result = db.query(KnowledgeMetadataBinding).filter(
            KnowledgeMetadataBinding.document_id == document_id
        ).delete()
        db.commit()
        return result

    @staticmethod
    def binding_exists(db: Session, knowledge_id: uuid.UUID, metadata_id: uuid.UUID, document_id: uuid.UUID) -> bool:
        return db.query(KnowledgeMetadataBinding).filter(
            and_(
                KnowledgeMetadataBinding.knowledge_id == knowledge_id,
                KnowledgeMetadataBinding.metadata_id == metadata_id,
                KnowledgeMetadataBinding.document_id == document_id,
            )
        ).first() is not None
