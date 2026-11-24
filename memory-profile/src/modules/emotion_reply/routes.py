"""Emotion reply endpoint for validated API-key requests from core/api."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from src.common.errors import BizCode
from src.common.external_api import external_api
from src.common.responses import ApiResponse, error_response, failure
from src.middleware.auth import SOURCE_EXTERNAL, MemoryPrincipal
from src.middleware.identity import require_memory_context
from src.schemas.emotion_reply import EmotionReplyData, EmotionReplyRequest

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Emotion Reply"])


@router.post("/memory/emotion-reply", response_model=ApiResponse[EmotionReplyData])
@external_api
async def emotion_reply(
    request: Request,
    context: Annotated[MemoryPrincipal, Depends(require_memory_context)],
    body: EmotionReplyRequest,
) -> ApiResponse[EmotionReplyData] | JSONResponse:
    # The trusted API proxy verifies the API key type, workspace binding, and tenant context.
    if (
        context.source != SOURCE_EXTERNAL
        or context.actor_kind != "apikey"
        or "authorization" in request.headers
    ):
        return error_response(
            request, 403, failure(BizCode.API_KEY_INVALID, "仅支持经 API 鉴权的服务请求")
        )

    runtime = request.app.state.runtime
    if not runtime.memory_enterprise.enabled:
        return error_response(
            request, 503, failure(BizCode.SERVICE_UNAVAILABLE, "情绪回应策略能力不可用")
        )

    try:
        data = await runtime.emotion_reply.resolve(
            body.message,
            context.trace_id,
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
        )
    except Exception:
        logger.error("emotion reply failed: request_id=%s fault=internal_error", context.trace_id)
        return error_response(
            request, 500, failure(BizCode.INTERNAL_ERROR, "情绪回应提示词获取失败")
        )
    return ApiResponse[EmotionReplyData](data=data, msg="情绪回应提示词获取成功")
