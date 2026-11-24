"""Adapt preview results and failures to the existing HTTP contract."""

import logging
from uuid import UUID

from fastapi import Request
from fastapi.responses import JSONResponse

from src.common.errors import BizCode
from src.common.responses import (
    ApiResponse,
    error_response,
    failure,
    http_error_response,
    server_error_response,
    success,
)
from src.i18n import get_request_language, t
from src.middleware.auth import SOURCE_INTERNAL, MemoryPrincipal
from src.runtime import ProfileRuntime
from src.schemas.reflection_trial import ReflectionTrialData, ReflectionTrialFailure
from src.services.memory_config.errors import ConfigNotFoundError
from src.services.memory_config_trial.reflection import ReflectionTrialService

logger = logging.getLogger(__name__)


class ReflectionHandler:
    def __init__(self, runtime: ProfileRuntime) -> None:
        self.service = ReflectionTrialService(runtime.postgres, runtime.model_service)

    async def run(
        self, context: MemoryPrincipal, config_id: UUID | int, request: Request
    ) -> ApiResponse[ReflectionTrialData | ReflectionTrialFailure] | JSONResponse:
        language = get_request_language(request)
        if context.source != SOURCE_INTERNAL or context.actor_kind != "actor":
            return error_response(
                request, 403, failure(BizCode.FORBIDDEN, t("reflection.trial.forbidden", language))
            )
        try:
            result = await self.service.run(
                config_id, context.workspace_id, context.tenant_id, locale=language
            )
            return success(result, msg=t("reflection.trial.success", language))
        except ConfigNotFoundError:
            return http_error_response(
                request,
                404,
                t("reflection.trial.config_not_found", language, config_id=str(config_id)),
            )
        except Exception:
            logger.exception("Reflection preview failed")
            return server_error_response(request, t("reflection.trial.unavailable", language))
