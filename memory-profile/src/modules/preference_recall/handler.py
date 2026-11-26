"""Adapt preference recall results and failures to the HTTP response contract."""

import logging

from fastapi import Request
from fastapi.responses import JSONResponse

from src.common.errors import BizCode, ProfileError
from src.common.responses import ApiResponse, error_response, failure
from src.middleware.auth import MemoryPrincipal
from src.runtime import ProfileRuntime
from src.schemas.preference_recall import (
    FullPreferenceRecallResult,
    PreferenceRecallRequest,
    PreferenceRecallResult,
)
from src.services.preference_recall import (
    PreferenceRecallService,
    PreferenceRecallValidationError,
    validate_preference_recall_request,
)

logger = logging.getLogger(__name__)
OTHER_ID_HEADER = "X-End-User-Other-Id"
_INVALID_REQUEST = "偏好召回请求参数不合法"
_UNAVAILABLE = "偏好召回服务暂时不可用"


def _require_other_id(value: str | None) -> str:
    other_id = (value or "").strip()
    if not other_id:
        raise ProfileError(BizCode.INVALID_PARAMETER, f"缺少 {OTHER_ID_HEADER} 请求头")
    return other_id


def _business_error(request: Request, exc: ProfileError) -> JSONResponse:
    return error_response(request, 200, failure(exc.code, exc.message, exc.detail, exc.data))


def _service_unavailable(request: Request, exc: Exception) -> JSONResponse:
    logger.warning("preference_recall_failed error=%s", type(exc).__name__)
    return error_response(request, 200, failure(BizCode.SERVICE_UNAVAILABLE, _UNAVAILABLE))


class PreferenceRecallHandler:
    def __init__(self, runtime: ProfileRuntime) -> None:
        self.settings = runtime.settings
        self.service = PreferenceRecallService(runtime)

    async def _parse_payload(self, request: Request) -> PreferenceRecallRequest:
        try:
            return validate_preference_recall_request(await request.json(), self.settings)
        except (ValueError, TypeError, PreferenceRecallValidationError) as exc:
            raise ProfileError(BizCode.INVALID_PARAMETER, _INVALID_REQUEST) from exc

    async def recall(
        self, request: Request, principal: MemoryPrincipal, other_id: str | None
    ) -> ApiResponse[PreferenceRecallResult] | JSONResponse:
        try:
            payload = await self._parse_payload(request)
            result = await self.service.recall(principal, _require_other_id(other_id), payload)
            return ApiResponse(data=result, msg="查询成功")
        except ProfileError as exc:
            return _business_error(request, exc)
        except Exception as exc:
            return _service_unavailable(request, exc)

    async def recall_full(
        self, request: Request, principal: MemoryPrincipal, other_id: str | None
    ) -> ApiResponse[FullPreferenceRecallResult] | JSONResponse:
        try:
            if await request.body():
                raise ProfileError(BizCode.INVALID_PARAMETER, _INVALID_REQUEST)
            result = await self.service.recall_full(principal, _require_other_id(other_id))
            return ApiResponse(data=result, msg="查询成功")
        except ProfileError as exc:
            return _business_error(request, exc)
        except Exception as exc:
            return _service_unavailable(request, exc)
