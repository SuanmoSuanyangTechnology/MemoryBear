"""Perceptual memory query service."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from src.common.normalization import datetime_to_epoch_ms
from src.repositories.perceptual import (
    MemoryPerceptualRepository,
)
from src.schemas.perceptual import (
    AudioModal,
    FileStorageService,
    LatestMemory,
    MemoryCount,
    MemoryTimeline,
    PerceptualContent,
    PerceptualType,
    TextModal,
    TimelineItem,
    VisualModal,
)

if TYPE_CHECKING:
    from src.runtime import ProfileRuntime


class PerceptualMemoryService:
    def __init__(
        self,
        runtime: ProfileRuntime | None = None,
        *,
        repository: MemoryPerceptualRepository | None = None,
    ) -> None:
        if repository is not None:
            self.repository = repository
        elif runtime is not None:
            self.repository = MemoryPerceptualRepository(runtime.postgres)
        else:
            raise ValueError("Either runtime or repository must be provided")

    async def count(self, *, end_user_id: UUID, workspace_id: UUID) -> MemoryCount:
        counts = await self.repository.count_by_type(end_user_id, workspace_id)
        return MemoryCount.model_validate(
            {
                "total": sum(counts.values()),
                "by_type": {
                    "vision": counts.get(PerceptualType.VISION.value, 0),
                    "audio": counts.get(PerceptualType.AUDIO.value, 0),
                    "text": counts.get(PerceptualType.TEXT.value, 0),
                    "conversation": counts.get(PerceptualType.CONVERSATION.value, 0),
                },
            }
        )

    async def timeline(
        self,
        *,
        end_user_id: UUID,
        workspace_id: UUID,
        perceptual_type: PerceptualType | None,
        page: int,
        page_size: int,
    ) -> MemoryTimeline:
        rows, total = await self.repository.timeline(
            end_user_id=end_user_id,
            workspace_id=workspace_id,
            perceptual_type=(int(perceptual_type) if perceptual_type is not None else None),
            page=page,
            page_size=page_size,
        )
        return MemoryTimeline.model_validate(
            {
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": (total + page_size - 1) // page_size,
                "memories": [self._timeline_item(row) for row in rows],
            }
        )

    async def latest(
        self,
        *,
        end_user_id: UUID,
        workspace_id: UUID,
        perceptual_type: PerceptualType,
    ) -> LatestMemory | None:
        row = await self.repository.latest(
            end_user_id=end_user_id,
            workspace_id=workspace_id,
            perceptual_type=int(perceptual_type),
        )
        if row is None:
            return None
        return LatestMemory.model_validate(self._latest_item(row, perceptual_type))

    def _timeline_item(self, row: dict[str, Any]) -> TimelineItem:
        meta_data = row.get("meta_data") or {}
        content = meta_data.get("content", {})
        parsed = (
            PerceptualContent(**content)
            if content
            else PerceptualContent(topic="Unknown", domain="Unknown", keywords=[])
        )
        return TimelineItem(
            id=row["id"],
            perceptual_type=PerceptualType(row["perceptual_type"]),
            storage_service=FileStorageService(row["storage_service"]),
            file_path=row["file_path"],
            file_name=row["file_name"],
            file_ext=row["file_ext"],
            summary=row["summary"],
            meta_data=meta_data,
            topic=parsed.topic,
            domain=parsed.domain,
            keywords=parsed.keywords,
            created_time=datetime_to_epoch_ms(row["created_time"]),
        )

    def _latest_item(self, row: dict[str, Any], perceptual_type: PerceptualType) -> dict[str, Any]:
        meta_data = row.get("meta_data") or {}
        modalities = meta_data.get("modalities")
        content = meta_data.get("content")
        if not modalities:
            raise ValueError(f"Modalities not defined, perceptual memory_id={row['id']}")
        if not content:
            raise ValueError(f"Content not defined, perceptual memory_id={row['id']}")
        parsed = PerceptualContent(**content)
        modal_type = {
            PerceptualType.VISION: VisualModal,
            PerceptualType.AUDIO: AudioModal,
            PerceptualType.TEXT: TextModal,
        }.get(perceptual_type)
        if modal_type is None:
            raise ValueError("Unsupported perceptual type")
        detail = modal_type(**modalities).model_dump()
        return {
            "id": str(row["id"]),
            "file_name": row["file_name"],
            "file_path": row["file_path"],
            "storage_type": row["storage_service"],
            "summary": row["summary"],
            "keywords": parsed.keywords,
            "topic": parsed.topic,
            "domain": parsed.domain,
            "created_time": datetime_to_epoch_ms(row["created_time"]),
            **detail,
        }
