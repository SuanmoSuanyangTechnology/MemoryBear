"""HTTP adaptation for the episodic-memory module."""

from __future__ import annotations

import logging

from src.common.errors import BizCode, ProfileError
from src.common.responses import ApiResponse, legacy_fail_response
from src.middleware.auth import MemoryPrincipal
from src.modules.episodic_memory.schemas import DetailsRequest, OverviewRequest
from src.repositories.episodic_memory import SceneCommunityNotFoundError
from src.runtime import ProfileRuntime
from src.services.episodic_memory import EpisodicMemoryService

logger = logging.getLogger(__name__)


class EpisodicMemoryHandler:
    def __init__(self, runtime: ProfileRuntime) -> None:
        self.service = EpisodicMemoryService(runtime)

    async def overview(self, context: MemoryPrincipal, body: OverviewRequest):
        try:
            data = await self.service.overview(
                context.workspace_id,
                body.end_user_id,
                body.category_l1,
                body.keyword or "",
                body.sort,
                body.page,
                body.pagesize,
            )
            return ApiResponse(data=data, msg="查询成功")
        except ProfileError as exc:
            return legacy_fail_response(exc.code, exc.message, exc.detail)
        except Exception:
            logger.exception("episodic overview failed")
            return legacy_fail_response(BizCode.INTERNAL_ERROR, "情景记忆查询失败，请重试")

    async def details(self, context: MemoryPrincipal, body: DetailsRequest):
        try:
            data = await self.service.details(
                context.workspace_id,
                body.end_user_id,
                body.scene_community_id,
                body.page,
                body.pagesize,
            )
            return ApiResponse(data=data, msg="查询成功")
        except ProfileError as exc:
            return legacy_fail_response(exc.code, exc.message, exc.detail)
        except SceneCommunityNotFoundError as exc:
            return legacy_fail_response(BizCode.INVALID_PARAMETER, str(exc))
        except Exception:
            logger.exception("episodic details failed")
            return legacy_fail_response(BizCode.INTERNAL_ERROR, "情景记忆查询失败，请重试")
