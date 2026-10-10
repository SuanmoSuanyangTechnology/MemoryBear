"""HTTP adapter for on-demand Coding Agent preference recall."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.controllers.service.mcp_auth_middleware import OTHER_ID_HEADER
from app.core.api_key_auth import get_current_api_key_auth, require_api_key_self_db
from app.core.error_codes import BizCode
from app.core.memory.storage_services.preference_engine.recall_service import (
    PreferenceRecallForbiddenError,
    PreferenceRecallValidationError,
    execute_preference_recall,
    validate_preference_recall_request,
)
from app.core.response_utils import fail, success
from app.db import get_async_db_context
from app.repositories.end_user_repository import EndUserRepository
from app.services.memory_config_service import MemoryConfigService

router = APIRouter(prefix="/memory/preference", tags=["V1 - Preference Recall API"])


@router.post("/recall")
@require_api_key_self_db(scopes=["memory"])
async def recall_coding_preferences_http(request: Request) -> JSONResponse:
    """Recall task-relevant preferences using API-key and end-user headers."""
    # A lone model-typed parameter would make FastAPI parse the whole body as ApiKeyAuth.
    api_key_auth = get_current_api_key_auth()
    other_id = request.headers.get(OTHER_ID_HEADER, "").strip()
    if not other_id:
        return JSONResponse(
            content=fail(BizCode.INVALID_PARAMETER, f"缺少 {OTHER_ID_HEADER} 请求头")
        )

    try:
        body: Any = await request.json()
    except (ValueError, TypeError):
        return JSONResponse(
            content=fail(BizCode.INVALID_PARAMETER, "偏好召回请求参数不合法")
        )
    try:
        payload = validate_preference_recall_request(body)
    except PreferenceRecallValidationError:
        return JSONResponse(
            content=fail(BizCode.INVALID_PARAMETER, "偏好召回请求参数不合法")
        )

    try:
        async with get_async_db_context() as db:
            end_user = await EndUserRepository(db).get_end_user_by_other_id_async(
                api_key_auth.workspace_id,
                other_id,
            )
            if end_user is None:
                return JSONResponse(content=fail(BizCode.USER_NOT_FOUND, "终端用户不存在"))
            end_user_id = str(end_user.id)
            config_id = await MemoryConfigService(db).get_workspace_active_config_id_async(
                api_key_auth.workspace_id
            )
    except Exception:
        return JSONResponse(
            content=fail(BizCode.SERVICE_UNAVAILABLE, "偏好召回服务暂时不可用")
        )

    try:
        preferences = await execute_preference_recall(
            config_id=config_id,
            workspace_id=api_key_auth.workspace_id,
            end_user_id=end_user_id,
            request_payload=payload,
        )
    except PreferenceRecallValidationError:
        return JSONResponse(
            content=fail(BizCode.INVALID_PARAMETER, "偏好召回请求参数不合法")
        )
    except PreferenceRecallForbiddenError:
        return JSONResponse(content=fail(BizCode.PERMISSION_DENIED, "无权访问该偏好资源"))
    except Exception:
        return JSONResponse(
            content=fail(BizCode.SERVICE_UNAVAILABLE, "偏好召回服务暂时不可用")
        )

    return JSONResponse(
        content=success(
            data={
                "preferences": [item.model_dump(mode="json") for item in preferences],
                "count": len(preferences),
            },
            msg="查询成功",
        )
    )
