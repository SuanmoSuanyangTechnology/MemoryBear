import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from sqlalchemy import and_, delete, select, update, func
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
    async def create_async(db: AsyncSession, obj: KnowledgeMetadata) -> KnowledgeMetadata:
        db.add(obj)
        await db.commit()
        await db.refresh(obj)
        return obj

    @staticmethod
    def get_by_id(db: Session, metadata_id: uuid.UUID) -> KnowledgeMetadata | None:
        return db.query(KnowledgeMetadata).filter(KnowledgeMetadata.id == metadata_id).first()

    @staticmethod
    async def get_by_id_async(db: AsyncSession, metadata_id: uuid.UUID) -> KnowledgeMetadata | None:
        result = await db.execute(select(KnowledgeMetadata).where(KnowledgeMetadata.id == metadata_id))
        return result.scalars().first()

    @staticmethod
    def get_by_knowledge_id(db: Session, knowledge_id: uuid.UUID) -> list[KnowledgeMetadata]:
        return db.query(KnowledgeMetadata).filter(
            KnowledgeMetadata.knowledge_id == knowledge_id
        ).all()

    @staticmethod
    async def get_by_knowledge_id_async(db: AsyncSession, knowledge_id: uuid.UUID) -> list[KnowledgeMetadata]:
        result = await db.execute(
            select(KnowledgeMetadata).where(KnowledgeMetadata.knowledge_id == knowledge_id)
        )
        return list(result.scalars().all())

    @staticmethod
    def get_by_knowledge_ids(db: Session, knowledge_ids: list[uuid.UUID]) -> list[KnowledgeMetadata]:
        if not knowledge_ids:
            return []
        return db.query(KnowledgeMetadata).filter(
            KnowledgeMetadata.knowledge_id.in_(knowledge_ids)
        ).all()

    @staticmethod
    async def get_by_knowledge_ids_async(db: AsyncSession, knowledge_ids: list[uuid.UUID]) -> list[KnowledgeMetadata]:
        if not knowledge_ids:
            return []
        result = await db.execute(
            select(KnowledgeMetadata).where(KnowledgeMetadata.knowledge_id.in_(knowledge_ids))
        )
        return list(result.scalars().all())

    @staticmethod
    def get_by_name(db: Session, knowledge_id: uuid.UUID, name: str) -> KnowledgeMetadata | None:
        return db.query(KnowledgeMetadata).filter(
            and_(
                KnowledgeMetadata.knowledge_id == knowledge_id,
                KnowledgeMetadata.name == name,
            )
        ).first()

    @staticmethod
    async def get_by_name_async(db: AsyncSession, knowledge_id: uuid.UUID, name: str) -> KnowledgeMetadata | None:
        result = await db.execute(
            select(KnowledgeMetadata).where(
                KnowledgeMetadata.knowledge_id == knowledge_id,
                KnowledgeMetadata.name == name,
            )
        )
        return result.scalars().first()

    @staticmethod
    def update(db: Session, metadata_id: uuid.UUID, update_data: dict) -> int:
        result = db.query(KnowledgeMetadata).filter(
            KnowledgeMetadata.id == metadata_id
        ).update(update_data)
        db.commit()
        return result

    @staticmethod
    async def update_async(db: AsyncSession, metadata_id: uuid.UUID, update_data: dict) -> int:
        result = await db.execute(
            update(KnowledgeMetadata).where(KnowledgeMetadata.id == metadata_id).values(update_data)
        )
        await db.commit()
        return result.rowcount or 0

    @staticmethod
    def delete(db: Session, metadata_id: uuid.UUID) -> int:
        result = db.query(KnowledgeMetadata).filter(
            KnowledgeMetadata.id == metadata_id
        ).delete()
        db.commit()
        return result

    @staticmethod
    async def delete_async(db: AsyncSession, metadata_id: uuid.UUID) -> int:
        result = await db.execute(delete(KnowledgeMetadata).where(KnowledgeMetadata.id == metadata_id))
        await db.commit()
        return result.rowcount or 0

    @staticmethod
    def delete_by_knowledge_id(db: Session, knowledge_id: uuid.UUID) -> int:
        result = db.query(KnowledgeMetadata).filter(
            KnowledgeMetadata.knowledge_id == knowledge_id
        ).delete()
        db.commit()
        return result

    @staticmethod
    async def delete_by_knowledge_id_async(db: AsyncSession, knowledge_id: uuid.UUID) -> int:
        result = await db.execute(delete(KnowledgeMetadata).where(KnowledgeMetadata.knowledge_id == knowledge_id))
        await db.commit()
        return result.rowcount or 0

    # === Binding Operations ===

    @staticmethod
    def create_binding(db: Session, obj: KnowledgeMetadataBinding) -> KnowledgeMetadataBinding:
        db.add(obj)
        db.commit()
        db.refresh(obj)
        return obj

    @staticmethod
    async def create_binding_async(db: AsyncSession, obj: KnowledgeMetadataBinding) -> KnowledgeMetadataBinding:
        db.add(obj)
        await db.commit()
        await db.refresh(obj)
        return obj

    @staticmethod
    def get_bindings_by_metadata_id(db: Session, metadata_id: uuid.UUID) -> list[KnowledgeMetadataBinding]:
        return db.query(KnowledgeMetadataBinding).filter(
            KnowledgeMetadataBinding.metadata_id == metadata_id
        ).all()

    @staticmethod
    async def get_bindings_by_metadata_id_async(
        db: AsyncSession,
        metadata_id: uuid.UUID,
    ) -> list[KnowledgeMetadataBinding]:
        result = await db.execute(
            select(KnowledgeMetadataBinding).where(KnowledgeMetadataBinding.metadata_id == metadata_id)
        )
        return list(result.scalars().all())

    @staticmethod
    def get_bindings_by_document_id(db: Session, document_id: uuid.UUID) -> list[KnowledgeMetadataBinding]:
        return db.query(KnowledgeMetadataBinding).filter(
            KnowledgeMetadataBinding.document_id == document_id
        ).all()

    @staticmethod
    async def get_bindings_by_document_id_async(
        db: AsyncSession,
        document_id: uuid.UUID,
    ) -> list[KnowledgeMetadataBinding]:
        result = await db.execute(
            select(KnowledgeMetadataBinding).where(KnowledgeMetadataBinding.document_id == document_id)
        )
        return list(result.scalars().all())

    @staticmethod
    def delete_bindings_by_metadata_id(db: Session, metadata_id: uuid.UUID) -> int:
        result = db.query(KnowledgeMetadataBinding).filter(
            KnowledgeMetadataBinding.metadata_id == metadata_id
        ).delete()
        db.commit()
        return result

    @staticmethod
    async def delete_bindings_by_metadata_id_async(db: AsyncSession, metadata_id: uuid.UUID) -> int:
        result = await db.execute(
            delete(KnowledgeMetadataBinding).where(KnowledgeMetadataBinding.metadata_id == metadata_id)
        )
        await db.commit()
        return result.rowcount or 0

    @staticmethod
    def delete_bindings_by_document_id(db: Session, document_id: uuid.UUID) -> int:
        result = db.query(KnowledgeMetadataBinding).filter(
            KnowledgeMetadataBinding.document_id == document_id
        ).delete()
        db.commit()
        return result

    @staticmethod
    async def delete_bindings_by_document_id_async(db: AsyncSession, document_id: uuid.UUID) -> int:
        result = await db.execute(
            delete(KnowledgeMetadataBinding).where(KnowledgeMetadataBinding.document_id == document_id)
        )
        await db.commit()
        return result.rowcount or 0

    @staticmethod
    def binding_exists(db: Session, knowledge_id: uuid.UUID, metadata_id: uuid.UUID, document_id: uuid.UUID) -> bool:
        return db.query(KnowledgeMetadataBinding).filter(
            and_(
                KnowledgeMetadataBinding.knowledge_id == knowledge_id,
                KnowledgeMetadataBinding.metadata_id == metadata_id,
                KnowledgeMetadataBinding.document_id == document_id,
            )
        ).first() is not None

    @staticmethod
    async def binding_exists_async(
        db: AsyncSession,
        knowledge_id: uuid.UUID,
        metadata_id: uuid.UUID,
        document_id: uuid.UUID,
    ) -> bool:
        result = await db.execute(
            select(KnowledgeMetadataBinding.id).where(
                KnowledgeMetadataBinding.knowledge_id == knowledge_id,
                KnowledgeMetadataBinding.metadata_id == metadata_id,
                KnowledgeMetadataBinding.document_id == document_id,
            )
        )
        return result.scalar_one_or_none() is not None

    @staticmethod
    def count_active_bindings_by_metadata_ids(
        db: Session,
        metadata_ids: list[uuid.UUID],
    ) -> dict[uuid.UUID, int]:
        if not metadata_ids:
            return {}

        from app.models.document_model import Document

        rows = (
            db.query(
                KnowledgeMetadataBinding.metadata_id,
                func.count(KnowledgeMetadataBinding.document_id),
            )
            .join(Document, Document.id == KnowledgeMetadataBinding.document_id)
            .filter(
                KnowledgeMetadataBinding.metadata_id.in_(metadata_ids),
                Document.status == 1,
            )
            .group_by(KnowledgeMetadataBinding.metadata_id)
            .all()
        )
        return {metadata_id: int(count) for metadata_id, count in rows}

    @staticmethod
    async def count_active_bindings_by_metadata_ids_async(
        db: AsyncSession,
        metadata_ids: list[uuid.UUID],
    ) -> dict[uuid.UUID, int]:
        if not metadata_ids:
            return {}

        from app.models.document_model import Document

        stmt = (
            select(
                KnowledgeMetadataBinding.metadata_id,
                func.count(KnowledgeMetadataBinding.document_id),
            )
            .join(Document, Document.id == KnowledgeMetadataBinding.document_id)
            .where(
                KnowledgeMetadataBinding.metadata_id.in_(metadata_ids),
                Document.status == 1,
            )
            .group_by(KnowledgeMetadataBinding.metadata_id)
        )
        result = await db.execute(stmt)
        return {metadata_id: int(count) for metadata_id, count in result.all()}
