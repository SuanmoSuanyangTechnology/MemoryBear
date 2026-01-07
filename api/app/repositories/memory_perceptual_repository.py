import uuid
from datetime import datetime
from typing import List, Tuple, Optional

from sqlalchemy import and_, desc
from sqlalchemy.orm import Session

from app.core.logging_config import get_db_logger
from app.models.memory_perceptual_model import MemoryPerceptualModel, PerceptualType, FileStorageType
from app.schemas.memory_perceptual_schema import PerceptualQuerySchema

db_logger = get_db_logger()


class MemoryPerceptualRepository:
    """Data Access Layer for perceptual memory"""

    def __init__(self, db: Session):
        self.db = db

    # ==================== Create and update ====================
    def create_perceptual_memory(
            self,
            end_user_id: uuid.UUID,
            perceptual_type: PerceptualType,
            file_path: str,
            file_name: str,
            file_ext: str,
            summary: Optional[str] = None,
            meta_data: Optional[dict] = None,
            storage_service: FileStorageType = FileStorageType.LOCAL

    ) -> MemoryPerceptualModel:

        """Create perceptual memory"""

        db_logger.debug(f"Creating perceptual memory: end_user_id={end_user_id}, "
                        f"type={perceptual_type}, file={file_name}")

        try:
            perceptual_memory = MemoryPerceptualModel(
                end_user_id=end_user_id,
                perceptual_type=perceptual_type,
                storage_service=storage_service,
                file_path=file_path,
                file_name=file_name,
                file_ext=file_ext,
                summary=summary,
                meta_data=meta_data,
                created_time=datetime.now()
            )

            self.db.add(perceptual_memory)
            self.db.flush()

            db_logger.info(f"Perceptual memory created successfully: id={perceptual_memory.id}, file={file_name}")
            return perceptual_memory

        except Exception as e:
            db_logger.error(f"Failed to create perceptual memory: end_user_id={end_user_id} - {str(e)}")
            raise

    # ==================== Query ====================
    def get_count_by_user_id(
            self,
            end_user_id: uuid.UUID,
    ):
        db_logger.debug(f"Querying perceptual memory Count: end_user_id={end_user_id}")

        try:
            count = self.db.query(MemoryPerceptualModel).filter(
                MemoryPerceptualModel.end_user_id == end_user_id
            ).count()
            return count
        except Exception as e:
            db_logger.error(f"Failed to query perceptual memory count: end_user_id={end_user_id} - {str(e)}")
            raise

    def get_count_by_type(
            self,
            end_user_id: uuid.UUID,
            perceptual_type: PerceptualType,
    ):
        db_logger.debug(f"Querying perceptual memory Count: end_user_id={end_user_id}, type={perceptual_type}")

        try:
            count = self.db.query(MemoryPerceptualModel).filter(
                MemoryPerceptualModel.end_user_id == end_user_id,
                MemoryPerceptualModel.perceptual_type == perceptual_type
            ).count()
            return count
        except Exception as e:
            db_logger.error(f"Failed to query perceptual memory count: end_user_id={end_user_id} - {str(e)}")
            raise

    def get_timeline(
            self,
            end_user_id: uuid.UUID,
            query: PerceptualQuerySchema
    ) -> Tuple[int, List[MemoryPerceptualModel]]:
        """Get the timeline of a user's perceptual memories"""
        db_logger.debug(f"Querying perceptual memory timeline: end_user_id={end_user_id}, filter={query.filter}")

        try:
            base_query = self.db.query(MemoryPerceptualModel).filter(
                MemoryPerceptualModel.end_user_id == end_user_id
            )

            if query.filter.type is not None:
                base_query = base_query.filter(
                    MemoryPerceptualModel.perceptual_type == query.filter.type
                )

            total_count = base_query.count()

            memories = base_query.order_by(
                desc(MemoryPerceptualModel.created_time)
            ).offset(
                (query.page - 1) * query.page_size
            ).limit(query.page_size).all()

            db_logger.info(
                f"Perceptual memory timeline query succeeded: end_user_id={end_user_id}, total={total_count}, returned={len(memories)}")
            return total_count, memories

        except Exception as e:
            db_logger.error(f"Failed to query perceptual memory timeline: end_user_id={end_user_id} - {str(e)}")
            raise

    def get_by_type(
            self,
            end_user_id: uuid.UUID,
            perceptual_type: PerceptualType,
            limit: int = 10,
            offset: int = 0
    ) -> List[MemoryPerceptualModel]:
        """Get memories by perceptual type"""
        db_logger.debug(f"Querying perceptual memories by type: end_user_id={end_user_id}, type={perceptual_type}")

        try:
            memories = self.db.query(MemoryPerceptualModel).filter(
                and_(
                    MemoryPerceptualModel.end_user_id == end_user_id,
                    MemoryPerceptualModel.perceptual_type == perceptual_type
                )
            ).order_by(
                desc(MemoryPerceptualModel.created_time)
            ).offset(offset).limit(limit).all()

            db_logger.debug(f"Query by type succeeded: count={len(memories)}")
            return memories

        except Exception as e:
            db_logger.error(f"Failed to query perceptual memories by type: end_user_id={end_user_id}, "
                            f"type={perceptual_type} - {str(e)}")
            raise
