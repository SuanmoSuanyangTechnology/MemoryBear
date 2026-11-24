"""供外部 Agent 使用的情绪回应指导接口。"""

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from app.core.api_key_auth import get_current_api_key_auth, require_api_key_self_db
from app.core.error_codes import BizCode
from app.core.response_utils import fail
from app.integrations.profile.proxy import memory_profile_proxy
from app.schemas.emotion_reply_schema import EmotionReplyRequest, EmotionReplyResponse
router = APIRouter(prefix="/memory/emotion-reply", tags=["Emotion Reply"])


@memory_profile_proxy(
    body_arg="body",
    body_fields=("message",),
    preserve_status=True,
)
async def _handle_emotion_reply(request: Request, body: EmotionReplyRequest) -> dict | Response:
    """声明已鉴权的情绪回应请求转发到 memory-profile 的参数。"""


@router.post("", response_model=EmotionReplyResponse, summary="获取本轮情绪回应提示词")
@require_api_key_self_db(scopes=["memory"])
async def emotion_reply(request: Request, body: EmotionReplyRequest) -> dict | Response:
    auth = get_current_api_key_auth()
    if auth.type != "service":
        return JSONResponse(
            status_code=403,
            content=fail(BizCode.API_KEY_INVALID, "仅支持 service 类型的 API Key"),
        )
    if (
        auth.workspace_id is None
        or auth.resource_id is None
        or auth.resource_id != auth.workspace_id
    ):
        return JSONResponse(
            status_code=403,
            content=fail(
                BizCode.API_KEY_INVALID_RESOURCE, "API Key 必须关联当前工作空间"
            ),
        )

    if auth.tenant_id is None:
        return JSONResponse(
            status_code=503,
            content=fail(BizCode.SERVICE_UNAVAILABLE, "情绪回应策略能力不可用"),
        )
    return await _handle_emotion_reply(request=request, body=body)
