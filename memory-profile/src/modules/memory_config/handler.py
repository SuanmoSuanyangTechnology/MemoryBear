"""Transport adaptation for the memory-configuration endpoints.

The monolith's memory-config handlers are not uniform: some failures are HTTP
200 with a business code, ``read_config_emotion``/``read_config_reflection`` use
HTTP 404/500, and a rejected create returns the non-standard quota envelope.
This module reproduces those shapes endpoint by endpoint instead of relying on
the global ``ProfileError`` handler.

Messages are translation keys resolved against the caller's language. Controller
messages (``success``/``error_response``) follow the legacy ``X-Language-Type`` rule;
``HTTPException``-shaped bodies and validation bodies follow the middleware
negotiation rule, matching the monolith.
"""

from __future__ import annotations

import logging
from typing import Any
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
from src.common.timing import now_ms
from src.i18n import get_header_language, get_request_language, t, translate_request
from src.infrastructure.scene_continuity import SceneContinuityError
from src.middleware.auth import SOURCE_EXTERNAL, MemoryPrincipal
from src.runtime import ProfileRuntime
from src.schemas.memory_config import (
    ActiveConfigResult,
    ConfigCreateResult,
    ConfigSummaryData,
    ConfigUpdateResult,
    DeleteConfigResult,
    EmotionConfigData,
    ExtractedConfigData,
    ForgettingConfigData,
    PersonalityAnchorData,
    PredictionConfigData,
    PreferenceConfigData,
    ReflectionConfigData,
    ReflectionMutationData,
    SceneCommunityConfigData,
    SceneConfigData,
    ValidateConfigResult,
)
from src.schemas.memory_config_requests import (
    CommunityPreviewBody,
    ConfigCreateBody,
    ConfigUpdateBody,
    EmotionUpdateBody,
    ExtractedUpdateBody,
    ForgettingUpdateBody,
    PersonalityAnchorUpdate,
    PredictionUpdateBody,
    PreferenceUpdateBody,
    ReflectionUpdateBody,
    SceneCommunityUpdateBody,
    SceneSplitDemoBody,
    SceneUpdateBody,
)
from src.schemas.scene_community import (
    CommunityPreviewCaseItem,
    CommunityPreviewResult,
)
from src.schemas.scene_demo import SceneSplitDemoResponse
from src.services.memory_config import (
    ConfigAccessDeniedError,
    ConfigNotFoundError,
    ConfigQueryError,
    DuplicateConfigNameError,
    InvalidConfigIdError,
    MemoryConfigMutationService,
    MemoryConfigQueryService,
    NoFieldsToUpdateError,
    WorkspaceNotFoundError,
)
from src.services.memory_config_trial.community_preview import (
    SceneCommunityPreviewService,
    UnsupportedPreviewCaseError,
)
from src.services.memory_config_trial.scene_demo import SceneSplitDemoService
from src.services.memory_config_trial.validation import ValidConfigService
from src.services.quota import QuotaDenied

logger = logging.getLogger(__name__)


def _quota_failure(request: Request, denied: QuotaDenied, language: str) -> JSONResponse:
    resource = t("memory_config.quota.resource", language)
    msg = t("memory_config.quota.exceeded", language, resource=resource)
    return error_response(
        request,
        402,
        {
            "success": False,
            "code": BizCode.QUOTA_EXCEEDED,
            "msg": msg,
            "message": msg,
            "error_code": "QUOTA_EXCEEDED",
            "data": {
                "resource": resource,
                "current_usage": denied.current_usage,
                "quota_limit": denied.quota_limit,
            },
            "error": msg,
            "time": now_ms(),
        },
        response_code=BizCode.QUOTA_EXCEEDED,
        error_code="QUOTA_EXCEEDED",
    )


def _duplicate_failure(request: Request, name: str, language: str) -> JSONResponse:
    msg = t("memory_config.config.duplicate_name", language)
    error = t("memory_config.config.duplicate_name_detail", language, config_name=name)
    return error_response(request, 400, failure(BizCode.BAD_REQUEST, msg, error))


class MemoryConfigHandler:
    def __init__(self, runtime: ProfileRuntime) -> None:
        self.runtime = runtime
        self.query = MemoryConfigQueryService(runtime.postgres)
        self.validation = ValidConfigService(runtime.postgres, runtime.model_service)
        self.mutation = MemoryConfigMutationService(
            runtime, query=self.query, validation=self.validation
        )
        # The BERT client owns an HTTP pool, so it lives on the runtime and is
        # created once per process rather than per request.
        self.scene_demo = SceneSplitDemoService(runtime.scene_continuity)

    # ── Reads ────────────────────────────────────────────────────────────

    async def read_personality_anchor(
        self, principal: MemoryPrincipal, config_id: Any, request: Request
    ) -> ApiResponse[PersonalityAnchorData] | JSONResponse:
        try:
            data = await self.query.personality_anchor(config_id, principal.workspace_id)
        except InvalidConfigIdError:
            return error_response(
                request,
                200,
                failure(
                    BizCode.INVALID_PARAMETER,
                    translate_request(
                        request,
                        "memory_config.error.invalid_config_id",
                        config_id=config_id,
                    ),
                ),
            )
        except (ConfigNotFoundError, ConfigAccessDeniedError):
            return error_response(
                request,
                200,
                failure(
                    BizCode.MEMORY_CONFIG_NOT_FOUND,
                    translate_request(request, "memory_config.error.not_found_or_denied"),
                ),
            )
        except Exception as exc:
            logger.exception("read_config_personality_anchor failed")
            return error_response(
                request,
                200,
                failure(
                    BizCode.INTERNAL_ERROR,
                    translate_request(request, "memory_config.error.query_failed"),
                    str(exc),
                ),
            )
        return success(data, translate_request(request, "memory_config.success.query"))

    async def read_extracted(
        self, principal: MemoryPrincipal, config_id: Any, request: Request
    ) -> ApiResponse[ExtractedConfigData] | JSONResponse:
        try:
            data = await self.query.extracted(config_id, principal.workspace_id)
        except InvalidConfigIdError as exc:
            if principal.source == SOURCE_EXTERNAL:
                return error_response(
                    request,
                    200,
                    failure(
                        BizCode.INVALID_PARAMETER,
                        translate_request(
                            request,
                            "memory_config.error.invalid_config_id",
                            config_id=config_id,
                        ),
                    ),
                )
            return server_error_response(request, str(exc), get_header_language(request))
        except ConfigNotFoundError:
            return error_response(
                request,
                200,
                failure(
                    BizCode.MEMORY_CONFIG_NOT_FOUND,
                    translate_request(request, "memory_config.error.config_not_found"),
                ),
            )
        except ConfigAccessDeniedError:
            return error_response(
                request,
                200,
                failure(
                    BizCode.MEMORY_CONFIG_NOT_FOUND,
                    translate_request(request, "memory_config.error.access_denied"),
                ),
            )
        except Exception as exc:
            logger.exception("read_config_extracted failed")
            return error_response(
                request,
                200,
                failure(
                    BizCode.INTERNAL_ERROR,
                    translate_request(request, "memory_config.error.query_config_failed"),
                    str(exc),
                ),
            )
        return success(data, translate_request(request, "memory_config.success.query"))

    async def read_forgetting(
        self, principal: MemoryPrincipal, config_id: Any, request: Request
    ) -> ApiResponse[ForgettingConfigData] | JSONResponse:
        try:
            data = await self.query.forgetting(config_id, principal.workspace_id)
        except InvalidConfigIdError as exc:
            if principal.source == SOURCE_EXTERNAL:
                return error_response(
                    request,
                    200,
                    failure(
                        BizCode.INVALID_PARAMETER,
                        translate_request(
                            request,
                            "memory_config.error.invalid_config_id",
                            config_id=config_id,
                        ),
                    ),
                )
            return server_error_response(request, str(exc), get_header_language(request))
        except ConfigNotFoundError:
            return error_response(
                request,
                200,
                failure(
                    BizCode.MEMORY_CONFIG_NOT_FOUND,
                    translate_request(request, "memory_config.error.config_not_found"),
                ),
            )
        except ConfigAccessDeniedError:
            return error_response(
                request,
                200,
                failure(
                    BizCode.MEMORY_CONFIG_NOT_FOUND,
                    translate_request(request, "memory_config.error.access_denied"),
                ),
            )
        except Exception as exc:
            logger.exception("read_config_forgetting failed")
            return error_response(
                request,
                200,
                failure(
                    BizCode.INTERNAL_ERROR,
                    translate_request(request, "memory_config.error.query_forgetting_failed"),
                    str(exc),
                ),
            )
        return success(data, translate_request(request, "memory_config.success.query"))

    async def read_emotion(
        self, principal: MemoryPrincipal, config_id: Any, request: Request
    ) -> ApiResponse[EmotionConfigData] | JSONResponse:
        language = get_request_language(request)
        try:
            data = await self.query.emotion(config_id, principal.workspace_id)
        except InvalidConfigIdError:
            return error_response(
                request,
                200,
                failure(
                    BizCode.INVALID_PARAMETER,
                    translate_request(
                        request,
                        "memory_config.error.invalid_config_id",
                        config_id=config_id,
                    ),
                ),
            )
        except (ConfigNotFoundError, ConfigAccessDeniedError):
            return error_response(
                request,
                200,
                failure(
                    BizCode.MEMORY_CONFIG_NOT_FOUND,
                    translate_request(request, "memory_config.error.not_found_or_denied"),
                ),
            )
        except Exception as exc:
            logger.exception("read_config_emotion failed")
            return http_error_response(
                request,
                500,
                t(
                    "memory_config.error.query_emotion_failed",
                    language,
                    error=str(exc),
                ),
                language,
            )
        return success(data, translate_request(request, "memory_config.success.emotion_query"))

    async def read_reflection(
        self, principal: MemoryPrincipal, config_id: Any, request: Request
    ) -> ApiResponse[ReflectionConfigData] | JSONResponse:
        language = get_request_language(request)
        try:
            data = await self.query.reflection(config_id, principal.workspace_id)
        except InvalidConfigIdError:
            return error_response(
                request,
                200,
                failure(
                    BizCode.INVALID_PARAMETER,
                    translate_request(
                        request,
                        "memory_config.error.invalid_config_id",
                        config_id=config_id,
                    ),
                ),
            )
        except (ConfigNotFoundError, ConfigAccessDeniedError):
            return error_response(
                request,
                200,
                failure(
                    BizCode.MEMORY_CONFIG_NOT_FOUND,
                    translate_request(request, "memory_config.error.not_found_or_denied"),
                ),
            )
        except Exception as exc:
            logger.exception("read_config_reflection failed")
            return http_error_response(
                request,
                500,
                t(
                    "memory_config.error.query_reflection_failed",
                    language,
                    error=str(exc),
                ),
                language,
            )
        return success(data, translate_request(request, "memory_config.success.reflection_query"))

    async def read_scene(
        self, principal: MemoryPrincipal, config_id: Any, request: Request
    ) -> ApiResponse[SceneConfigData] | JSONResponse:
        try:
            data = await self.query.scene(config_id, principal.workspace_id)
        except InvalidConfigIdError:
            return error_response(
                request,
                200,
                failure(
                    BizCode.INVALID_PARAMETER,
                    translate_request(
                        request,
                        "memory_config.error.invalid_config_id",
                        config_id=config_id,
                    ),
                ),
            )
        except (ConfigNotFoundError, ConfigAccessDeniedError):
            return error_response(
                request,
                200,
                failure(
                    BizCode.MEMORY_CONFIG_NOT_FOUND,
                    translate_request(request, "memory_config.error.not_found_or_denied"),
                ),
            )
        except Exception as exc:
            logger.exception("read_config_scene failed")
            return server_error_response(request, str(exc), get_header_language(request))
        return success(data, translate_request(request, "memory_config.success.query"))

    async def read_scene_community(
        self, principal: MemoryPrincipal, config_id: Any, request: Request
    ) -> ApiResponse[SceneCommunityConfigData] | JSONResponse:
        try:
            data = await self.query.scene_community(config_id, principal.workspace_id)
        except InvalidConfigIdError as exc:
            logger.warning("read_config_scene_community invalid id: %s", exc)
            return server_error_response(request, str(exc), get_header_language(request))
        except (ConfigNotFoundError, ConfigAccessDeniedError):
            return error_response(
                request,
                200,
                failure(
                    BizCode.MEMORY_CONFIG_NOT_FOUND,
                    translate_request(request, "memory_config.error.not_found_or_denied"),
                ),
            )
        except Exception as exc:
            logger.exception("read_config_scene_community failed")
            return server_error_response(request, str(exc), get_header_language(request))
        return success(data, translate_request(request, "memory_config.success.query"))

    async def read_prediction(
        self, principal: MemoryPrincipal, config_id: Any, request: Request
    ) -> ApiResponse[PredictionConfigData] | JSONResponse:
        try:
            data = await self.query.prediction(config_id, principal.workspace_id)
        except (ConfigNotFoundError, ConfigAccessDeniedError):
            return error_response(
                request,
                200,
                failure(
                    BizCode.MEMORY_CONFIG_NOT_FOUND,
                    translate_request(request, "memory_config.error.not_found_or_denied"),
                ),
            )
        except Exception as exc:
            logger.exception("read_config_prediction failed")
            return error_response(
                request,
                200,
                failure(
                    BizCode.INTERNAL_ERROR,
                    translate_request(request, "memory_config.error.query_failed"),
                    str(exc),
                ),
            )
        return success(data, translate_request(request, "memory_config.success.query"))

    async def read_all_config(
        self, principal: MemoryPrincipal, request: Request
    ) -> ApiResponse[list[ConfigSummaryData]] | JSONResponse:
        try:
            data = await self.query.read_all(principal.workspace_id)
        except Exception as exc:
            logger.exception("read_all_config failed")
            return error_response(
                request,
                200,
                failure(
                    BizCode.INTERNAL_ERROR,
                    translate_request(request, "memory_config.error.query_all_failed"),
                    str(exc),
                ),
            )
        return success(data, translate_request(request, "memory_config.success.query"))

    async def read_preference(
        self, principal: MemoryPrincipal, config_id: UUID, request: Request
    ) -> ApiResponse[PreferenceConfigData] | JSONResponse:
        try:
            data = await self.query.preference(config_id, principal.workspace_id)
        except (ConfigNotFoundError, ConfigAccessDeniedError):
            return error_response(
                request,
                200,
                failure(
                    BizCode.MEMORY_CONFIG_NOT_FOUND,
                    translate_request(request, "memory_config.error.not_found_or_denied"),
                ),
            )
        return success(data, translate_request(request, "memory_config.success.query"))

    # ── Writes ───────────────────────────────────────────────────────────

    async def update_personality_anchor(
        self,
        principal: MemoryPrincipal,
        body: PersonalityAnchorUpdate,
        request: Request,
    ) -> ApiResponse[PersonalityAnchorData] | JSONResponse:
        try:
            data = await self.mutation.update_personality(
                body.config_id,
                principal.workspace_id,
                body.model_dump(exclude={"config_id"}),
            )
        except ConfigAccessDeniedError:
            return error_response(
                request,
                200,
                failure(
                    BizCode.MEMORY_CONFIG_NOT_FOUND,
                    translate_request(request, "memory_config.error.access_denied"),
                ),
            )
        except ConfigNotFoundError:
            return error_response(
                request,
                200,
                failure(
                    BizCode.MEMORY_CONFIG_NOT_FOUND,
                    translate_request(request, "memory_config.error.not_found_or_denied"),
                ),
            )
        except Exception as exc:
            logger.exception("update_config_personality_anchor failed")
            return error_response(
                request,
                200,
                failure(
                    BizCode.INTERNAL_ERROR,
                    translate_request(request, "memory_config.error.update_failed"),
                    str(exc),
                ),
            )
        return success(data, translate_request(request, "memory_config.success.update"))

    async def create_config(
        self,
        principal: MemoryPrincipal,
        body: ConfigCreateBody,
        request: Request,
    ) -> ApiResponse[ConfigCreateResult] | JSONResponse:
        language = get_request_language(request)
        try:
            result = await self.mutation.create(
                tenant_id=principal.tenant_id,
                workspace_id=principal.workspace_id,
                body=body,
            )
        except QuotaDenied as denied:
            return _quota_failure(request, denied, language)
        except DuplicateConfigNameError as exc:
            return _duplicate_failure(request, exc.config_name, get_header_language(request))
        except ConfigQueryError as exc:
            return error_response(
                request,
                200,
                failure(
                    BizCode.INTERNAL_ERROR,
                    translate_request(request, "memory_config.error.create_failed"),
                    str(exc),
                ),
            )
        except Exception as exc:
            logger.exception("create_config failed")
            return error_response(
                request,
                200,
                failure(
                    BizCode.INTERNAL_ERROR,
                    translate_request(request, "memory_config.error.create_failed"),
                    str(exc),
                ),
            )
        return success(result, translate_request(request, "memory_config.success.create"))

    async def update_config(
        self, principal: MemoryPrincipal, body: ConfigUpdateBody, request: Request
    ) -> ApiResponse[ConfigUpdateResult] | JSONResponse:
        try:
            result = await self.mutation.update_basic(body, principal.workspace_id)
        except InvalidConfigIdError as exc:
            if principal.source == SOURCE_EXTERNAL:
                return error_response(
                    request,
                    200,
                    failure(
                        BizCode.INVALID_PARAMETER,
                        translate_request(
                            request,
                            "memory_config.error.invalid_config_id",
                            config_id=body.config_id,
                        ),
                    ),
                )
            return server_error_response(request, str(exc), get_header_language(request))
        except NoFieldsToUpdateError:
            return error_response(
                request,
                200,
                failure(
                    BizCode.INVALID_PARAMETER,
                    translate_request(request, "memory_config.error.no_fields_to_update"),
                    translate_request(request, "memory_config.error.no_fields_to_update_detail"),
                ),
            )
        except ConfigNotFoundError:
            return error_response(
                request,
                200,
                failure(
                    BizCode.MEMORY_CONFIG_NOT_FOUND,
                    translate_request(request, "memory_config.error.config_not_found"),
                ),
            )
        except ConfigAccessDeniedError:
            return error_response(
                request,
                200,
                failure(
                    BizCode.MEMORY_CONFIG_NOT_FOUND,
                    translate_request(request, "memory_config.error.access_denied"),
                ),
            )
        except Exception as exc:
            logger.exception("update_config failed")
            return error_response(
                request,
                200,
                failure(
                    BizCode.INTERNAL_ERROR,
                    translate_request(request, "memory_config.error.update_config_failed"),
                    str(exc),
                ),
            )
        return success(result, translate_request(request, "memory_config.success.update"))

    async def update_config_extracted(
        self, principal: MemoryPrincipal, body: ExtractedUpdateBody, request: Request
    ) -> ApiResponse[ConfigUpdateResult] | JSONResponse:
        try:
            result = await self.mutation.update_extracted(body, principal.workspace_id)
        except InvalidConfigIdError as exc:
            if principal.source == SOURCE_EXTERNAL:
                return error_response(
                    request,
                    200,
                    failure(
                        BizCode.INVALID_PARAMETER,
                        translate_request(
                            request,
                            "memory_config.error.invalid_config_id",
                            config_id=body.config_id,
                        ),
                    ),
                )
            return server_error_response(request, str(exc), get_header_language(request))
        except ConfigNotFoundError:
            return error_response(
                request,
                200,
                failure(
                    BizCode.MEMORY_CONFIG_NOT_FOUND,
                    translate_request(request, "memory_config.error.config_not_found"),
                ),
            )
        except ConfigAccessDeniedError:
            return error_response(
                request,
                200,
                failure(
                    BizCode.MEMORY_CONFIG_NOT_FOUND,
                    translate_request(request, "memory_config.error.access_denied"),
                ),
            )
        except Exception as exc:
            logger.exception("update_config_extracted failed")
            return error_response(
                request,
                200,
                failure(
                    BizCode.INTERNAL_ERROR,
                    translate_request(request, "memory_config.error.update_config_failed"),
                    str(exc),
                ),
            )
        return success(result, translate_request(request, "memory_config.success.update"))

    async def update_config_forgetting(
        self, principal: MemoryPrincipal, body: ForgettingUpdateBody, request: Request
    ) -> ApiResponse[ForgettingConfigData] | JSONResponse:
        try:
            result = await self.mutation.update_forgetting(body, principal.workspace_id)
        except InvalidConfigIdError as exc:
            if principal.source == SOURCE_EXTERNAL:
                return error_response(
                    request,
                    200,
                    failure(
                        BizCode.INVALID_PARAMETER,
                        translate_request(
                            request,
                            "memory_config.error.invalid_config_id",
                            config_id=body.config_id,
                        ),
                    ),
                )
            return server_error_response(request, str(exc), get_header_language(request))
        except ConfigNotFoundError:
            return error_response(
                request,
                200,
                failure(
                    BizCode.MEMORY_CONFIG_NOT_FOUND,
                    translate_request(request, "memory_config.error.config_not_found"),
                ),
            )
        except ConfigAccessDeniedError:
            return error_response(
                request,
                200,
                failure(
                    BizCode.MEMORY_CONFIG_NOT_FOUND,
                    translate_request(request, "memory_config.error.access_denied"),
                ),
            )
        except Exception as exc:
            logger.exception("update_config_forgetting failed")
            return error_response(
                request,
                200,
                failure(
                    BizCode.INTERNAL_ERROR,
                    translate_request(request, "memory_config.error.update_forgetting_failed"),
                    str(exc),
                ),
            )
        return success(result, translate_request(request, "memory_config.success.update"))

    async def update_config_emotion(
        self, principal: MemoryPrincipal, body: EmotionUpdateBody, request: Request
    ) -> ApiResponse[EmotionConfigData] | JSONResponse:
        language = get_request_language(request)
        try:
            result = await self.mutation.update_emotion(body, principal.workspace_id)
        except InvalidConfigIdError as exc:
            if principal.source == SOURCE_EXTERNAL:
                return error_response(
                    request,
                    200,
                    failure(
                        BizCode.INVALID_PARAMETER,
                        translate_request(
                            request,
                            "memory_config.error.invalid_config_id",
                            config_id=body.config_id,
                        ),
                    ),
                )
            return http_error_response(request, 400, str(exc), language)
        except (ConfigNotFoundError, ConfigAccessDeniedError):
            return error_response(
                request,
                200,
                failure(
                    BizCode.MEMORY_CONFIG_NOT_FOUND,
                    translate_request(request, "memory_config.error.not_found_or_denied"),
                ),
            )
        except ValueError as exc:
            if principal.source == SOURCE_EXTERNAL:
                return error_response(
                    request,
                    200,
                    failure(
                        BizCode.INVALID_PARAMETER,
                        translate_request(
                            request,
                            "memory_config.error.invalid_config_id",
                            config_id=body.config_id,
                        ),
                    ),
                )
            return http_error_response(request, 400, str(exc), language)
        except Exception as exc:
            logger.exception("update_config_emotion failed")
            return http_error_response(
                request,
                500,
                t(
                    "memory_config.error.update_emotion_failed",
                    language,
                    error=str(exc),
                ),
                language,
            )
        return success(result, translate_request(request, "memory_config.success.emotion_update"))

    async def update_config_reflection(
        self, principal: MemoryPrincipal, body: ReflectionUpdateBody, request: Request
    ) -> ApiResponse[ReflectionMutationData] | JSONResponse:
        language = get_request_language(request)
        if body.config_id is None:
            return http_error_response(
                request,
                400,
                t("memory_config.error.missing_config_id", language),
                language,
            )
        try:
            result = await self.mutation.update_reflection(body, principal.workspace_id)
        except InvalidConfigIdError as exc:
            if principal.source == SOURCE_EXTERNAL:
                return error_response(
                    request,
                    200,
                    failure(
                        BizCode.INVALID_PARAMETER,
                        translate_request(
                            request,
                            "memory_config.error.invalid_config_id",
                            config_id=body.config_id,
                        ),
                    ),
                )
            return http_error_response(request, 400, str(exc), language)
        except (ConfigNotFoundError, ConfigAccessDeniedError):
            return error_response(
                request,
                200,
                failure(
                    BizCode.MEMORY_CONFIG_NOT_FOUND,
                    translate_request(request, "memory_config.error.not_found_or_denied"),
                ),
            )
        except ValueError as exc:
            return http_error_response(
                request,
                400,
                t(
                    "memory_config.error.invalid_parameter_with_error",
                    language,
                    error=str(exc),
                ),
                language,
            )
        except Exception as exc:
            logger.exception("update_config_reflection failed")
            return http_error_response(
                request,
                500,
                t(
                    "memory_config.error.save_reflection_failed",
                    language,
                    error=str(exc),
                ),
                language,
            )
        return success(
            result, translate_request(request, "memory_config.success.reflection_update")
        )

    async def delete_config(
        self, principal: MemoryPrincipal, config_id: Any, request: Request
    ) -> ApiResponse[DeleteConfigResult] | JSONResponse:
        try:
            result = await self.mutation.delete(config_id, principal.workspace_id)
        except InvalidConfigIdError as exc:
            if principal.source == SOURCE_EXTERNAL:
                return error_response(
                    request,
                    200,
                    failure(
                        BizCode.INVALID_PARAMETER,
                        translate_request(
                            request,
                            "memory_config.error.invalid_config_id",
                            config_id=config_id,
                        ),
                    ),
                )
            return server_error_response(request, str(exc), get_header_language(request))
        except ConfigNotFoundError:
            return error_response(
                request,
                200,
                failure(
                    BizCode.MEMORY_CONFIG_NOT_FOUND,
                    translate_request(request, "memory_config.error.config_not_found"),
                ),
            )
        except ConfigAccessDeniedError:
            return error_response(
                request,
                200,
                failure(
                    BizCode.MEMORY_CONFIG_NOT_FOUND,
                    translate_request(request, "memory_config.error.access_denied"),
                ),
            )
        except Exception as exc:
            logger.exception("delete_config failed")
            return error_response(
                request,
                200,
                failure(
                    BizCode.INTERNAL_ERROR,
                    translate_request(request, "memory_config.error.delete_failed"),
                    str(exc),
                ),
            )

        message = translate_request(request, result["message_key"])
        data = {
            "config_id": str(config_id),
            "is_default": result.get("is_default", False),
        }
        if result["status"] == "error":
            return error_response(request, 200, failure(BizCode.FORBIDDEN, message))
        if result["status"] == "warning":
            return error_response(request, 200, failure(BizCode.RESOURCE_IN_USE, message))
        return success(data, message)

    async def update_config_scene(
        self, principal: MemoryPrincipal, body: SceneUpdateBody, request: Request
    ) -> ApiResponse[SceneConfigData] | JSONResponse:
        try:
            result = await self.mutation.update_scene(body, principal.workspace_id)
        except (ConfigNotFoundError, ConfigAccessDeniedError):
            return error_response(
                request,
                200,
                failure(
                    BizCode.MEMORY_CONFIG_NOT_FOUND,
                    translate_request(request, "memory_config.error.not_found_or_denied"),
                ),
            )
        except Exception as exc:
            logger.exception("update_config_scene failed")
            return server_error_response(request, str(exc), get_header_language(request))
        return success(result, translate_request(request, "memory_config.success.update"))

    async def update_config_scene_community(
        self, principal: MemoryPrincipal, body: SceneCommunityUpdateBody, request: Request
    ) -> ApiResponse[SceneCommunityConfigData] | JSONResponse:
        try:
            result = await self.mutation.update_scene_community(body, principal.workspace_id)
        except (ConfigNotFoundError, ConfigAccessDeniedError):
            return error_response(
                request,
                200,
                failure(
                    BizCode.MEMORY_CONFIG_NOT_FOUND,
                    translate_request(request, "memory_config.error.not_found_or_denied"),
                ),
            )
        except Exception as exc:
            logger.exception("update_config_scene_community failed")
            return server_error_response(request, str(exc), get_header_language(request))
        return success(result, translate_request(request, "memory_config.success.update"))

    async def update_config_preference(
        self, principal: MemoryPrincipal, body: PreferenceUpdateBody, request: Request
    ) -> ApiResponse[PreferenceConfigData] | JSONResponse:
        try:
            result = await self.mutation.update_preference(
                body,
                principal.workspace_id,
                operator=str(principal.actor_id),
            )
        except ValueError as exc:
            return error_response(
                request,
                200,
                failure(
                    BizCode.INVALID_PARAMETER,
                    translate_request(request, "memory_config.error.preference_invalid"),
                    str(exc),
                ),
            )
        except (ConfigNotFoundError, ConfigAccessDeniedError):
            return error_response(
                request,
                200,
                failure(
                    BizCode.MEMORY_CONFIG_NOT_FOUND,
                    translate_request(request, "memory_config.error.not_found_or_denied"),
                ),
            )
        except Exception as exc:
            logger.exception("update_config_preference failed")
            return error_response(
                request,
                200,
                failure(
                    BizCode.INTERNAL_ERROR,
                    translate_request(request, "memory_config.error.update_config_failed"),
                    str(exc),
                ),
            )
        return success(result, translate_request(request, "memory_config.success.update"))

    # ── Management-only ──────────────────────────────────────────────────

    async def validate_active_config(
        self, principal: MemoryPrincipal, request: Request
    ) -> ApiResponse[ValidateConfigResult] | JSONResponse:
        language = get_header_language(request)
        try:
            config_id = await self.query.active_config_id(principal.workspace_id)
        except Exception:
            config_id = None
        if config_id is None:
            return success(
                {
                    "valid": False,
                    "warnings": [
                        {"message": t("memory_config.workspace.no_active_config", language)}
                    ],
                }
            )
        try:
            result = await self.validation.valid_config(config_id, locale=language)
        except Exception as exc:
            logger.exception("validate_active_config failed")
            return server_error_response(request, str(exc), language)
        return success(result)

    async def update_config_prediction(
        self,
        principal: MemoryPrincipal,
        body: PredictionUpdateBody,
        request: Request,
    ) -> ApiResponse[PredictionConfigData] | JSONResponse:
        try:
            result = await self.mutation.update_prediction(body, principal.workspace_id)
        except (ConfigNotFoundError, ConfigAccessDeniedError):
            return error_response(
                request,
                200,
                failure(
                    BizCode.MEMORY_CONFIG_NOT_FOUND,
                    translate_request(request, "memory_config.error.not_found_or_denied"),
                ),
            )
        except Exception as exc:
            logger.exception("update_config_prediction failed")
            return server_error_response(request, str(exc), get_header_language(request))
        return success(result, translate_request(request, "memory_config.success.update"))

    async def active_config(
        self,
        principal: MemoryPrincipal,
        config_id: UUID,
        request: Request,
    ) -> ApiResponse[ActiveConfigResult] | JSONResponse:
        language = get_header_language(request)
        try:
            result = await self.mutation.active(principal.workspace_id, config_id, language)
        except WorkspaceNotFoundError:
            return error_response(
                request,
                200,
                failure(
                    BizCode.INVALID_PARAMETER,
                    t("common.workspace.not_found", language),
                ),
            )
        except ConfigNotFoundError:
            return error_response(
                request,
                200,
                failure(
                    BizCode.INVALID_PARAMETER,
                    t(
                        "memory_config.config.not_found",
                        language,
                        config_id=str(config_id),
                    ),
                ),
            )
        except Exception as exc:
            logger.exception("active_config failed")
            return server_error_response(request, str(exc), language)
        return success(result)

    async def scene_split_demo(
        self, principal: MemoryPrincipal, body: SceneSplitDemoBody, request: Request
    ) -> ApiResponse[SceneSplitDemoResponse] | JSONResponse:
        language = get_header_language(request)
        try:
            row = await self.query.owned_row(body.config_id, principal.workspace_id)
        except (InvalidConfigIdError, ConfigNotFoundError, ConfigAccessDeniedError):
            row = None
        if row is None:
            return error_response(
                request,
                200,
                failure(
                    BizCode.MEMORY_CONFIG_NOT_FOUND,
                    t("memory_config.scene_demo.config_not_found", language),
                ),
            )
        threshold = (
            body.scene_threshold if body.scene_threshold is not None else row["scene_threshold"]
        )
        window_size = (
            body.scene_history_window_size
            if body.scene_history_window_size is not None
            else row["scene_history_window_size"]
        )
        min_turns = (
            body.scene_min_turns if body.scene_min_turns is not None else row["scene_min_turns"]
        )
        max_turns = (
            body.scene_max_turns if body.scene_max_turns is not None else row["scene_max_turns"]
        )
        if min_turns >= max_turns:
            return error_response(
                request,
                200,
                failure(
                    BizCode.INVALID_PARAMETER,
                    t("memory_config.scene_demo.invalid_turn_range", language),
                ),
            )
        history_messages = [
            message for message in body.history_messages if message and message.strip()
        ]
        try:
            result = await self.scene_demo.run(
                history_messages=history_messages,
                current_query=body.current_query,
                current_scene_turns=len(history_messages),
                threshold=threshold,
                window_size=window_size,
                min_turns=min_turns,
                max_turns=max_turns,
                locale=language,
            )
        except SceneContinuityError as exc:
            logger.warning("scene split demo model call failed: %s", exc)
            return error_response(
                request,
                200,
                failure(
                    BizCode.INTERNAL_ERROR,
                    t("memory_config.scene_demo.model_unavailable", language),
                    str(exc),
                ),
            )
        return success(result, t("memory_config.success.query", language))

    async def preview_cases(
        self, request: Request
    ) -> ApiResponse[list[CommunityPreviewCaseItem]] | JSONResponse:
        language = get_header_language(request)
        data = SceneCommunityPreviewService.list_cases(locale=language)
        return success(data, t("memory_config.success.query", language))

    async def preview(
        self, principal: MemoryPrincipal, body: CommunityPreviewBody, request: Request
    ) -> ApiResponse[CommunityPreviewResult] | JSONResponse:
        language = get_header_language(request)
        try:
            config = await self.query.scene_community(
                body.config_id, principal.workspace_id
            )
        except (ConfigNotFoundError, ConfigAccessDeniedError):
            return error_response(
                request,
                200,
                failure(
                    BizCode.MEMORY_CONFIG_NOT_FOUND,
                    t("memory_config.error.not_found_or_denied", language),
                ),
            )
        except Exception as exc:
            logger.exception("preview config lookup failed")
            return server_error_response(request, str(exc), language)
        try:
            result = SceneCommunityPreviewService.build(
                body.preview_case,
                config.candidate_community_limit,
                locale=language,
            )
        except UnsupportedPreviewCaseError:
            return error_response(
                request,
                200,
                failure(
                    BizCode.INVALID_PARAMETER,
                    t("memory_config.error.preview_case_invalid", language),
                    t("memory_config.error.preview_case_invalid_detail", language),
                ),
            )
        return success(result, t("memory_config.success.query", language))
