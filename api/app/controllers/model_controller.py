"""模型管理面前缀代理（M7-4）：``/api/models*`` → model-service ``/internal/v1/models*``。

管理面 27 端点已于 M7-3 迁至 model-service；老单体不再持模型读写路径，本控制器只做
同路径直转——``/api`` 前缀换 ``/internal/v1``、查询串原样转发、请求/响应头白名单
透传、JWT 身份换成 ``X-Model-*`` 内部头，响应缓冲回传（409+data.impact / 422 信封 /
201 / 分页 / i18n 文案全部保真）。

姿态（spec §9 D2）：无运行期开关、无本地回退；服务不可达/超时 → 503 fail-fast。
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, Response

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.dependencies import CurrentUserSnapshot, get_current_user_async
from app.i18n.service import get_translation_service
from app.integrations.model.contracts import ModelCallContext
from app.integrations.model.errors import ModelServiceClientError
from app.integrations.model.runtime import get_model_service_client

router = APIRouter(prefix="/models", tags=["Models"], include_in_schema=False)

_METHODS = ["GET", "POST", "PUT", "DELETE"]


async def _forward(request: Request, current_user: CurrentUserSnapshot) -> Response:
    context = ModelCallContext(
        actor_id=current_user.id,
        actor_name=current_user.username,
        tenant_id=current_user.tenant_id,
        workspace_id=current_user.current_workspace_id,
        trace_id=getattr(request.state, "trace_id", "") or uuid.uuid4().hex,
    )
    try:
        return await get_model_service_client().forward(request, context)
    except ModelServiceClientError as exc:
        message = get_translation_service().translate(
            "errors.common.service_unavailable",
            getattr(request.state, "language", None),
        )
        raise BusinessException(
            message, BizCode.SERVICE_UNAVAILABLE, cause=exc
        ) from exc


# 两条路由缺一不可：``/{path:path}`` 不匹配裸 ``/api/models``
@router.api_route("", methods=_METHODS, name="proxy_model_collection")
async def proxy_model_collection(
    request: Request,
    current_user: CurrentUserSnapshot = Depends(get_current_user_async),
) -> Response:
    return await _forward(request, current_user)


@router.api_route("/{path:path}", methods=_METHODS, name="proxy_model_path")
async def proxy_model_path(
    request: Request,
    current_user: CurrentUserSnapshot = Depends(get_current_user_async),
) -> Response:
    return await _forward(request, current_user)
