"""Perceptual memory query service."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from src.common.errors import BizCode, ProfileError
from src.common.normalization import datetime_to_epoch_ms
from src.modules.perceptual.constants import FileStorageService, PerceptualType
from src.modules.perceptual.repository.memory_perceptual import (
    MemoryPerceptualRepository,
)
from src.runtime import ProfileRuntime

logger = logging.getLogger(__name__)


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

    async def _ensure_user(self, end_user_id: UUID, workspace_id: UUID) -> None:
        if not await self.repository.check_user_in_workspace(end_user_id, workspace_id):
            raise ProfileError(
                BizCode.USER_NOT_FOUND,
                "终端用户不存在",
                detail="end_user not found in current workspace",
                status_code=404,
            )

    async def count(self, *, end_user_id: UUID, workspace_id: UUID) -> dict[str, Any]:
        await self._ensure_user(end_user_id, workspace_id)
        counts = await self.repository.count_by_type(end_user_id)
        return {
            "total": sum(counts.values()),
            "by_type": {
                "vision": counts.get(PerceptualType.VISION.value, 0),
                "audio": counts.get(PerceptualType.AUDIO.value, 0),
                "text": counts.get(PerceptualType.TEXT.value, 0),
                "conversation": counts.get(PerceptualType.CONVERSATION.value, 0),
            },
        }

    async def timeline(
        self,
        *,
        end_user_id: UUID,
        workspace_id: UUID,
        perceptual_type: PerceptualType | None,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        await self._ensure_user(end_user_id, workspace_id)
        rows, total = await self.repository.timeline(
            end_user_id=end_user_id,
            perceptual_type=(
                int(perceptual_type) if perceptual_type is not None else None
            ),
            page=page,
            page_size=page_size,
        )
        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
            "memories": [self._timeline_item(row) for row in rows],
        }

    async def latest(
        self,
        *,
        end_user_id: UUID,
        workspace_id: UUID,
        perceptual_type: PerceptualType,
    ) -> dict[str, Any] | None:
        await self._ensure_user(end_user_id, workspace_id)
        row = await self.repository.latest(
            end_user_id=end_user_id,
            perceptual_type=int(perceptual_type),
        )
        if row is None:
            return None
        return self._latest_item(row, perceptual_type)

    @staticmethod
    def _content(row: dict[str, Any]) -> dict[str, Any]:
        meta_data = row.get("meta_data")
        if not isinstance(meta_data, dict):
            raise ProfileError(
                BizCode.INTERNAL_ERROR,
                "Perceptual memory metadata is invalid",
                status_code=500,
            )
        content = meta_data.get("content")
        if not isinstance(content, dict):
            raise ProfileError(
                BizCode.INTERNAL_ERROR,
                "Perceptual memory content is invalid",
                status_code=500,
            )
        return content

    def _timeline_item(self, row: dict[str, Any]) -> dict[str, Any]:
        meta_data = row.get("meta_data") or {}
        content = (
            meta_data.get("content")
            if isinstance(meta_data, dict) and isinstance(meta_data.get("content"), dict)
            else {}
        )
        storage_service = FileStorageService(int(row["storage_service"])).value
        return {
            "id": str(row["id"]),
            "perceptual_type": int(row["perceptual_type"]),
            "storage_service": storage_service,
            "file_path": row["file_path"],
            "file_name": row["file_name"],
            "file_ext": row["file_ext"],
            "summary": row["summary"],
            "meta_data": meta_data,
            "topic": content.get("topic", "Unknown"),
            "domain": content.get("domain", "Unknown"),
            "keywords": content.get("keywords", []),
            "created_time": datetime_to_epoch_ms(row["created_time"]),
        }

    def _latest_item(
        self, row: dict[str, Any], perceptual_type: PerceptualType
    ) -> dict[str, Any]:
        meta_data = row.get("meta_data")
        if not isinstance(meta_data, dict):
            raise ProfileError(
                BizCode.INTERNAL_ERROR,
                f"Modalities not defined, perceptual memory_id={row['id']}",
                status_code=500,
            )
        modalities = meta_data.get("modalities")
        content = self._content(row)
        if not isinstance(modalities, dict):
            raise ProfileError(
                BizCode.INTERNAL_ERROR,
                f"Modalities not defined, perceptual memory_id={row['id']}",
                status_code=500,
            )

        if perceptual_type == PerceptualType.VISION:
            scene = modalities.get("scene")
            if not isinstance(scene, list):
                raise ProfileError(
                    BizCode.INTERNAL_ERROR,
                    f"Modalities not defined, perceptual memory_id={row['id']}",
                    status_code=500,
                )
            detail = {"scene": scene}
        elif perceptual_type == PerceptualType.AUDIO:
            speaker_count = modalities.get("speaker_count")
            if not isinstance(speaker_count, int):
                raise ProfileError(
                    BizCode.INTERNAL_ERROR,
                    f"Modalities not defined, perceptual memory_id={row['id']}",
                    status_code=500,
                )
            detail = {"speaker_count": speaker_count}
        else:
            required = ("section_count", "title", "first_line")
            if any(key not in modalities for key in required):
                raise ProfileError(
                    BizCode.INTERNAL_ERROR,
                    f"Modalities not defined, perceptual memory_id={row['id']}",
                    status_code=500,
                )
            detail = {
                "section_count": modalities["section_count"],
                "title": modalities["title"],
                "first_line": modalities["first_line"],
            }

        keywords = content.get("keywords")
        topic = content.get("topic")
        domain = content.get("domain")
        if not isinstance(keywords, list) or not isinstance(topic, str) or not isinstance(
            domain, str
        ):
            raise ProfileError(
                BizCode.INTERNAL_ERROR,
                f"Content not defined, perceptual memory_id={row['id']}",
                status_code=500,
            )

        return {
            "id": str(row["id"]),
            "file_name": row["file_name"],
            "file_path": row["file_path"],
            "storage_type": row["storage_service"],
            "summary": row["summary"],
            "keywords": keywords,
            "topic": topic,
            "domain": domain,
            "created_time": datetime_to_epoch_ms(row["created_time"]),
            **detail,
        }
