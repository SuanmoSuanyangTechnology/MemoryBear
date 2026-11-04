"""HTTP handlers for perceptual memory reads."""

from __future__ import annotations

from src.common.errors import BizCode, ProfileError
from src.common.responses import ApiResponse
from src.common.validation import parse_required_uuid
from src.middleware.auth import MemoryPrincipal
from src.runtime import ProfileRuntime
from src.schemas.perceptual import LatestMemory, MemoryCount, MemoryTimeline, PerceptualType
from src.services.perceptual import PerceptualMemoryService

CountApiResponse = ApiResponse[MemoryCount]
TimelineApiResponse = ApiResponse[MemoryTimeline]
LatestApiResponse = ApiResponse[LatestMemory]


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

    async def count(self, context: MemoryPrincipal, end_user_id: str) -> CountApiResponse:
        data = await self._read(
            self.service.count,
            "Failed to fetch memory statistics",
            end_user_id=parse_required_uuid(end_user_id),
            workspace_id=context.workspace_id,
        )
        return CountApiResponse(data=data, msg="Memory statistics retrieved successfully")

    async def timeline(
        self,
        context: MemoryPrincipal,
        end_user_id: str,
        *,
        perceptual_type: PerceptualType | None,
        page: int,
        page_size: int,
    ) -> TimelineApiResponse:
        data = await self._read(
            self.service.timeline,
            "Failed to fetch perceptual memory timeline",
            end_user_id=parse_required_uuid(end_user_id),
            workspace_id=context.workspace_id,
            perceptual_type=perceptual_type,
            page=page,
            page_size=page_size,
        )
        return TimelineApiResponse(
            data=data,
            msg="Perceptual memory timeline retrieved successfully",
        )

    async def latest(
        self,
        context: MemoryPrincipal,
        end_user_id: str,
        perceptual_type: PerceptualType,
    ) -> LatestApiResponse:
        label = {
            PerceptualType.VISION: "visual",
            PerceptualType.AUDIO: "audio",
            PerceptualType.TEXT: "text",
        }[perceptual_type]
        data = await self._read(
            self.service.latest,
            f"Failed to fetch latest {label} memory",
            end_user_id=parse_required_uuid(end_user_id),
            workspace_id=context.workspace_id,
            perceptual_type=perceptual_type,
        )
        payload = data if data is not None else {}
        return LatestApiResponse(
            data=payload,
            msg=(
                f"Latest {label} memory retrieved successfully"
                if data is not None
                else f"No {label} memory available"
            ),
        )

    @staticmethod
    async def _read(method, message: str, **kwargs):
        try:
            return await method(**kwargs)
        except Exception as exc:
            raise ProfileError(BizCode.INTERNAL_ERROR, message, status_code=200) from exc
