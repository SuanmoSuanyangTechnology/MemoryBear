"""HTTP handlers for perceptual memory reads."""

from __future__ import annotations

from typing import Any

from src.common.responses import ApiResponse
from src.common.validation import parse_required_uuid
from src.middleware.auth import MemoryPrincipal
from src.modules.perceptual.constants import PerceptualType
from src.modules.perceptual.service import PerceptualMemoryService
from src.runtime import ProfileRuntime

ObjectApiResponse = ApiResponse[dict[str, Any]]
LatestApiResponse = ApiResponse[dict[str, Any]]


class PerceptualMemoryHandler:
    def __init__(
        self,
        runtime: ProfileRuntime | None = None,
        *,
        service: PerceptualMemoryService | None = None,
    ) -> None:
        if service is not None:
            self.service = service
        elif runtime is not None:
            self.service = PerceptualMemoryService(runtime)
        else:
            raise ValueError("Either runtime or service must be provided")

    async def count(
        self, context: MemoryPrincipal, end_user_id: str
    ) -> ObjectApiResponse:
        data = await self.service.count(
            end_user_id=parse_required_uuid(end_user_id),
            workspace_id=context.workspace_id,
        )
        return ObjectApiResponse(data=data, msg="Memory statistics retrieved successfully")

    async def timeline(
        self,
        context: MemoryPrincipal,
        end_user_id: str,
        *,
        perceptual_type: PerceptualType | None,
        page: int,
        page_size: int,
    ) -> ObjectApiResponse:
        data = await self.service.timeline(
            end_user_id=parse_required_uuid(end_user_id),
            workspace_id=context.workspace_id,
            perceptual_type=perceptual_type,
            page=page,
            page_size=page_size,
        )
        return ObjectApiResponse(
            data=data,
            msg="Perceptual memory timeline retrieved successfully",
        )

    async def latest(
        self,
        context: MemoryPrincipal,
        end_user_id: str,
        perceptual_type: PerceptualType,
    ) -> LatestApiResponse:
        data = await self.service.latest(
            end_user_id=parse_required_uuid(end_user_id),
            workspace_id=context.workspace_id,
            perceptual_type=perceptual_type,
        )
        payload = data if data is not None else {}
        labels = {
            PerceptualType.VISION: "visual",
            PerceptualType.AUDIO: "audio",
            PerceptualType.TEXT: "text",
        }
        label = labels[perceptual_type]
        return LatestApiResponse(
            data=payload,
            msg=(
                f"Latest {label} memory retrieved successfully"
                if payload
                else f"No {label} memory available"
            ),
        )
