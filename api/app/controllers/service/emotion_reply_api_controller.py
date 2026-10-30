"""供外部 Agent 使用的情绪回应指导接口。"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.api_key_auth import get_current_api_key_auth, require_api_key_self_db
from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.response_utils import fail, success
from app.core.trace import get_trace_id
from app.schemas.emotion_reply_schema import EmotionReplyRequest, EmotionReplyResponse
from app.services.emotion_reply_service import resolve_emotion_reply

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/memory/emotion-reply", tags=["Emotion Reply"])


@router.post("", response_model=EmotionReplyResponse, summary="获取本轮情绪回应提示词")
@require_api_key_self_db(scopes=["memory"])
async def emotion_reply(request: Request, body: EmotionReplyRequest):
    auth = get_current_api_key_auth()
    if auth.type != "service":
        return JSONResponse(
            status_code=403,
            content=fail(BizCode.API_KEY_INVALID, "仅支持 service 类型的 API Key"),
        )
    if auth.workspace_id is None or auth.resource_id is None or auth.resource_id != auth.workspace_id:
        return JSONResponse(
            status_code=403,
            content=fail(BizCode.API_KEY_INVALID_RESOURCE, "API Key 必须关联当前工作空间"),
        )
    try:
        data = await resolve_emotion_reply(body.message)
    except BusinessException as exc:
        if exc.code != BizCode.SERVICE_UNAVAILABLE:
            raise
        return JSONResponse(
            status_code=503,
            content=fail(BizCode.SERVICE_UNAVAILABLE, "情绪回应策略能力不可用"),
        )
    except Exception:
        # 不把上游响应、SQL 参数或用户原文写进日志或返回调用方。
        logger.error(
            "[EmotionReplyAPI] request_id=%s fault_type=internal_error", get_trace_id(),
        )
        return JSONResponse(
            status_code=500,
            content=fail(BizCode.INTERNAL_ERROR, "情绪回应提示词获取失败"),
        )
    return success(data=data.model_dump(), msg="情绪回应提示词获取成功")
