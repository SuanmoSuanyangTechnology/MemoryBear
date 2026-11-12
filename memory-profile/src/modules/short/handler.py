"""Legacy-compatible short-term HTTP responses."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from uuid import UUID

from error_core.envelope import fail, success
from fastapi import Request
from fastapi.responses import JSONResponse
from neo4j.exceptions import ServiceUnavailable, SessionExpired, TransientError
from sqlalchemy.exc import SQLAlchemyError

from src.common.errors import BizCode
from src.common.normalization import parse_uuid
from src.i18n import get_request_language
from src.middleware.auth import MemoryPrincipal

if TYPE_CHECKING:
    from src.runtime import ProfileRuntime
from src.schemas.short_memory import MemoDateType, MemoScope
from src.services.short_memory import ShortService, ShortTermMemoService

logger = logging.getLogger(__name__)


class ShortMemoryHandler:
    def __init__(self, runtime: ProfileRuntime):
        self.service = ShortService(runtime)
        self.memo_service = ShortTermMemoService(runtime)

    async def short_term(
        self,
        context: MemoryPrincipal,
        end_user_id: str,
    ) -> dict | JSONResponse:
        user_id = parse_uuid(end_user_id)
        if user_id is None or await self.service.active_user(user_id, context.workspace_id) is None:
            return JSONResponse(
                status_code=404,
                content=fail(
                    BizCode.NOT_FOUND,
                    "终端用户不存在或不可访问",
                    "resource not found",
                ),
            )
        result = await self.service.short_term(str(user_id))
        return success(data=result, msg="短期记忆系统数据获取成功")

    async def memos(
        self,
        context: MemoryPrincipal,
        end_user_id: UUID,
        scope: MemoScope,
        date_type: MemoDateType,
        page: int,
        pagesize: int,
        request: Request,
    ) -> dict | JSONResponse:
        try:
            workspace_id = context.workspace_id
            if workspace_id is None:
                return JSONResponse(
                    status_code=400,
                    content=fail(
                        BizCode.INVALID_PARAMETER,
                        "请先切换到一个工作空间",
                        "current_workspace_id is None",
                    ),
                )
            user_id = await self.service.active_user(end_user_id, workspace_id)
            if user_id is None:
                return JSONResponse(
                    status_code=404,
                    content=fail(
                        BizCode.NOT_FOUND,
                        "终端用户不存在或不可访问",
                        "resource not found",
                    ),
                )
            result = await self.memo_service.list_memos(
                str(user_id),
                scope,
                date_type,
                page,
                pagesize,
            )
            return success(data=result.model_dump(), locale=get_request_language(request))
        except (ServiceUnavailable, SessionExpired, TransientError, SQLAlchemyError):
            logger.exception("Short-term memo database unavailable")
            return JSONResponse(
                status_code=503,
                content=fail(
                    BizCode.INTERNAL_ERROR,
                    "短期记忆服务暂时不可用",
                    "database unavailable",
                ),
            )
        except Exception:
            logger.exception("Failed to list short-term memos")
            return JSONResponse(
                status_code=500,
                content=fail(
                    BizCode.INTERNAL_ERROR,
                    "短期记忆查询失败",
                    "internal error",
                ),
            )
