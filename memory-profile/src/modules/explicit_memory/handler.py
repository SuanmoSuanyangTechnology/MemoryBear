"""HTTP-facing handlers for explicit-memory read endpoints (T07–T10).

Thin transport layer in the analytics/implicit-memory style: it translates
service results into the monolith-compatible ``ApiResponse`` envelope and
converts failures into the legacy ``fail(...)`` envelope (HTTP 200, never a
4xx/5xx on these paths — that is the monolith's contract).  All validation,
fallback strings and graph-result shaping live in
``src.services.explicit_memory``.
"""

from __future__ import annotations

import logging

from fastapi.responses import JSONResponse

from src.common.errors import BizCode, ProfileError
from src.common.responses import ApiResponse, legacy_fail_response
from src.middleware.auth import MemoryPrincipal
from src.runtime import ProfileRuntime
from src.schemas.explicit_memory import (
    DetailsRequest,
    EpisodicListData,
    ExplicitDetailsData,
    OverviewData,
    SemanticMemoryItem,
)
from src.services.explicit_memory import ExplicitMemoryService

logger = logging.getLogger(__name__)


class ExplicitMemoryHandler:
    """Envelope adapter between the transport and the explicit-memory service."""

    def __init__(
        self,
        runtime: ProfileRuntime | None = None,
        *,
        service: ExplicitMemoryService | None = None,
    ) -> None:
        if service is not None:
            self.service = service
        elif runtime is not None:
            self.service = ExplicitMemoryService(runtime)
        else:
            raise ValueError("Either runtime or service must be provided")

    # ── T07 overview ─────────────────────────────────────────────────────────

    async def get_overview(
        self, context: MemoryPrincipal, end_user_id: str
    ) -> ApiResponse[OverviewData] | JSONResponse:
        try:
            data = await self.service.overview(
                end_user_id=end_user_id, workspace_id=context.workspace_id
            )
        except ProfileError as exc:
            return legacy_fail_response(exc.code, exc.message, exc.detail)
        except Exception as exc:  # noqa: BLE001 — parity: monolith wraps as INTERNAL_ERROR
            logger.error("显性记忆总览查询失败: %s", exc, exc_info=True)
            return legacy_fail_response(BizCode.INTERNAL_ERROR, "显性记忆总览查询失败", str(exc))
        return ApiResponse[OverviewData](data=data, msg="查询成功")

    # ── T08 episodics ────────────────────────────────────────────────────────

    async def get_episodics(
        self,
        context: MemoryPrincipal,
        end_user_id: str,
        page: int,
        pagesize: int,
        start_date: int | None,
        end_date: int | None,
        episodic_type: str,
    ) -> ApiResponse[EpisodicListData] | JSONResponse:
        try:
            data = await self.service.episodics_page(
                end_user_id=end_user_id,
                workspace_id=context.workspace_id,
                page=page,
                pagesize=pagesize,
                start_date=start_date,
                end_date=end_date,
                episodic_type=episodic_type,
            )
        except ProfileError as exc:
            return legacy_fail_response(exc.code, exc.message, exc.detail)
        except Exception as exc:  # noqa: BLE001
            logger.error("情景记忆分页查询失败: %s", exc, exc_info=True)
            return legacy_fail_response(BizCode.INTERNAL_ERROR, "情景记忆分页查询失败", str(exc))
        return ApiResponse[EpisodicListData](data=data, msg="查询成功")

    # ── T09 semantics ────────────────────────────────────────────────────────

    async def get_semantics(
        self, context: MemoryPrincipal, end_user_id: str
    ) -> ApiResponse[list[SemanticMemoryItem]] | JSONResponse:
        try:
            items = await self.service.semantics(
                end_user_id=end_user_id, workspace_id=context.workspace_id
            )
        except ProfileError as exc:
            return legacy_fail_response(exc.code, exc.message, exc.detail)
        except Exception as exc:  # noqa: BLE001
            logger.error("语义记忆列表查询失败: %s", exc, exc_info=True)
            return legacy_fail_response(BizCode.INTERNAL_ERROR, "语义记忆列表查询失败", str(exc))
        return ApiResponse[list[SemanticMemoryItem]](data=items, msg="查询成功")

    # ── T10 details ──────────────────────────────────────────────────────────

    async def get_details(
        self, context: MemoryPrincipal, body: DetailsRequest
    ) -> ApiResponse[ExplicitDetailsData] | JSONResponse:
        try:
            data = await self.service.details(
                end_user_id=body.end_user_id,
                memory_id=body.memory_id,
                workspace_id=context.workspace_id,
            )
        except ProfileError as exc:
            return legacy_fail_response(exc.code, exc.message, exc.detail)
        except Exception as exc:  # noqa: BLE001
            logger.error("显性记忆详情查询失败: %s", exc, exc_info=True)
            return legacy_fail_response(BizCode.INTERNAL_ERROR, "显性记忆详情查询失败", str(exc))
        return ApiResponse[ExplicitDetailsData](data=data, msg="查询成功")
