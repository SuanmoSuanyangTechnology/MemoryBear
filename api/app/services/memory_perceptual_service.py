import uuid
from typing import Dict, Any, Optional

from sqlalchemy.orm import Session

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.logging_config import get_business_logger
from app.models.memory_perceptual_model import PerceptualType, FileStorageType
from app.repositories.memory_perceptual_repository import MemoryPerceptualRepository
from app.schemas.memory_perceptual_schema import (
    PerceptualQuerySchema,
    PerceptualTimelineResponse,
    PerceptualMemoryItem,
    AudioModal, Content, VideoModal, TextModal
)

business_logger = get_business_logger()


class MemoryPerceptualService:
    def __init__(self, db: Session):
        self.db = db
        self.repository = MemoryPerceptualRepository(db)

    def get_memory_count(self, end_user_id: uuid.UUID) -> Dict[str, Any]:
        """Retrieve perceptual memory statistics for a user."""
        business_logger.info(f"Fetching perceptual memory statistics: end_user_id={end_user_id}")
        try:
            total_count = self.repository.get_count_by_user_id(end_user_id=end_user_id)

            vision_count = self.repository.get_count_by_type(end_user_id, PerceptualType.VISION)
            audio_count = self.repository.get_count_by_type(end_user_id, PerceptualType.AUDIO)
            text_count = self.repository.get_count_by_type(end_user_id, PerceptualType.TEXT)
            conversation_count = self.repository.get_count_by_type(end_user_id, PerceptualType.CONVERSATION)

            stats = {
                "total": total_count,
                "by_type": {
                    "vision": vision_count,
                    "audio": audio_count,
                    "text": text_count,
                    "conversation": conversation_count
                }
            }

            business_logger.info(f"Memory statistics fetched successfully: total={total_count}")
            return stats

        except Exception as e:
            business_logger.error(f"Failed to fetch memory statistics: {str(e)}")
            raise BusinessException(f"Failed to fetch memory statistics: {str(e)}", BizCode.DB_ERROR)

    def _get_latest_memory_by_type(
            self,
            end_user_id: uuid.UUID,
            perceptual_type: PerceptualType
    ) -> Optional[dict[str, Any]]:
        """Internal helper to retrieve the latest memory by type."""
        business_logger.info(f"Fetching latest {perceptual_type.name.lower()} memory: end_user_id={end_user_id}")
        try:
            memories = self.repository.get_by_type(
                end_user_id=end_user_id,
                perceptual_type=perceptual_type,
                limit=1,
                offset=0
            )
            if not memories:
                business_logger.info(f"No {perceptual_type.name.lower()} memory found: end_user_id={end_user_id}")
                return None

            memory = memories[0]
            meta_data = memory.meta_data or {}
            modalities = meta_data.get("modalities")
            content = meta_data.get("content")

            if not modalities:
                raise BusinessException(f"Modalities not defined, perceptual memory_id={memory.id}", BizCode.DB_ERROR)
            if not content:
                raise BusinessException(f"Content not defined, perceptual memory_id={memory.id}", BizCode.DB_ERROR)
            content = Content(**content)
            match perceptual_type:
                case PerceptualType.VISION:
                    modal = VideoModal(**modalities)
                case PerceptualType.AUDIO:
                    modal = AudioModal(**modalities)
                case PerceptualType.TEXT:
                    modal = TextModal(**modalities)
                case _:
                    raise BusinessException("Unsupported perceptual type", BizCode.DB_ERROR)
            detail = modal.model_dump()

            result = {
                "id": str(memory.id),
                "file_name": memory.file_name,
                "file_path": memory.file_path,
                "storage_type": memory.storage_service,
                "summary": memory.summary,
                "keywords": content.keywords,
                "topic": content.topic,
                "domain": content.domain,
                "created_time": int(memory.created_time.timestamp()*1000),
                **detail
            }

            business_logger.info(
                f"Latest {perceptual_type.name.lower()} memory retrieved successfully: file={memory.file_name}")
            return result

        except Exception as e:
            business_logger.error(f"Failed to fetch latest {perceptual_type.name.lower()} memory: {str(e)}")
            raise BusinessException(f"Failed to fetch latest {perceptual_type.name.lower()} memory: {str(e)}",
                                    BizCode.DB_ERROR)

    def get_latest_visual_memory(self, end_user_id: uuid.UUID) -> Optional[Dict[str, Any]]:
        return self._get_latest_memory_by_type(end_user_id, PerceptualType.VISION)

    def get_latest_audio_memory(self, end_user_id: uuid.UUID) -> Optional[Dict[str, Any]]:
        return self._get_latest_memory_by_type(end_user_id, PerceptualType.AUDIO)

    def get_latest_text_memory(self, end_user_id: uuid.UUID) -> Optional[Dict[str, Any]]:
        return self._get_latest_memory_by_type(end_user_id, PerceptualType.TEXT)

    def get_time_line(self, end_user_id: uuid.UUID, query: PerceptualQuerySchema) -> PerceptualTimelineResponse:
        """Retrieve a timeline of perceptual memories for a user."""
        business_logger.info(f"Fetching perceptual memory timeline: "
                             f"end_user_id={end_user_id}, filter={query.filter}")

        try:
            if query.page < 1:
                raise BusinessException("Page number must be greater than 0", BizCode.INVALID_PARAMETER)
            if query.page_size < 1 or query.page_size > 100:
                raise BusinessException("Page size must be between 1 and 100", BizCode.INVALID_PARAMETER)

            total_count, memories = self.repository.get_timeline(end_user_id, query)

            memory_items = []
            for memory in memories:
                memory_item = PerceptualMemoryItem(
                    id=memory.id,
                    perceptual_type=PerceptualType(memory.perceptual_type),
                    file_path=memory.file_path,
                    file_name=memory.file_name,
                    file_ext=memory.file_ext,
                    summary=memory.summary,
                    created_time=int(memory.created_time.timestamp()*1000),
                    storage_type=FileStorageType(memory.storage_service),
                )
                memory_items.append(memory_item)

            timeline_response = PerceptualTimelineResponse(
                total=total_count,
                page=query.page,
                page_size=query.page_size,
                total_pages=(total_count + query.page_size - 1) // query.page_size,
                memories=memory_items
            )

            business_logger.info(f"Perceptual memory timeline retrieved successfully: "
                                 f"total={total_count}, returned={len(memories)}")
            return timeline_response

        except BusinessException:
            raise
        except Exception as e:
            business_logger.error(f"Failed to fetch perceptual memory timeline: {str(e)}")
            raise BusinessException(f"Failed to fetch perceptual memory timeline: {str(e)}", BizCode.DB_ERROR)
