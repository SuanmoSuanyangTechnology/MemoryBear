"""Internal memory-configuration routes.

Paths and parameter placement mirror the monolith exactly, because the API proxy
forwards the caller's own path and query string unchanged. Endpoints the API-key
face can reach carry ``@external_api``; management-only endpoints do not.

All request bodies are declared with their Pydantic models; ``create_config``
keeps only ``config_name``/``config_desc`` and ignores every other field.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from src.common.external_api import external_api
from src.common.responses import ApiResponse
from src.middleware.auth import MemoryPrincipal
from src.middleware.identity import require_memory_context
from src.modules.memory_config.handler import MemoryConfigHandler
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

router = APIRouter(prefix="/memory_config", tags=["Memory Config"])
community_router = APIRouter(prefix="/memory/configs/community", tags=["Memory Config"])
Context = Annotated[MemoryPrincipal, Depends(require_memory_context)]


def _handler(request: Request) -> MemoryConfigHandler:
    return MemoryConfigHandler(request.app.state.runtime)


# ── Reads ────────────────────────────────────────────────────────────────────


@router.get(
    "/read_config_personality_anchor",
    response_model=ApiResponse[PersonalityAnchorData],
)
@external_api
async def read_config_personality_anchor(
    request: Request,
    context: Context,
    config_id: Annotated[UUID, Query()],
) -> ApiResponse[PersonalityAnchorData] | JSONResponse:
    return await _handler(request).read_personality_anchor(context, config_id, request)


@router.get("/read_config_extracted", response_model=ApiResponse[ExtractedConfigData])
@external_api
async def read_config_extracted(
    request: Request,
    context: Context,
    config_id: str = Query(..., description="config_id"),
) -> ApiResponse[ExtractedConfigData] | JSONResponse:
    return await _handler(request).read_extracted(context, config_id, request)


@router.get("/read_config_forgetting", response_model=ApiResponse[ForgettingConfigData])
@external_api
async def read_config_forgetting(
    request: Request,
    context: Context,
    config_id: str = Query(..., description="config_id"),
) -> ApiResponse[ForgettingConfigData] | JSONResponse:
    return await _handler(request).read_forgetting(context, config_id, request)


@router.get("/read_config_emotion", response_model=ApiResponse[EmotionConfigData])
@external_api
async def read_config_emotion(
    request: Request,
    context: Context,
    config_id: str = Query(..., description="config_id"),
) -> ApiResponse[EmotionConfigData] | JSONResponse:
    return await _handler(request).read_emotion(context, config_id, request)


@router.get("/read_config_reflection", response_model=ApiResponse[ReflectionConfigData])
@external_api
async def read_config_reflection(
    request: Request,
    context: Context,
    config_id: str = Query(..., description="config_id"),
) -> ApiResponse[ReflectionConfigData] | JSONResponse:
    return await _handler(request).read_reflection(context, config_id, request)


@router.get("/read_config_scene", response_model=ApiResponse[SceneConfigData])
@external_api
async def read_config_scene(
    request: Request,
    context: Context,
    config_id: str = Query(..., description="config_id"),
) -> ApiResponse[SceneConfigData] | JSONResponse:
    return await _handler(request).read_scene(context, config_id, request)


@router.get(
    "/read_config_scene_community",
    response_model=ApiResponse[SceneCommunityConfigData],
)
@external_api
async def read_config_scene_community(
    request: Request,
    context: Context,
    config_id: str = Query(..., description="config_id"),
) -> ApiResponse[SceneCommunityConfigData] | JSONResponse:
    return await _handler(request).read_scene_community(context, config_id, request)


@router.get("/read_all_config", response_model=ApiResponse[list[ConfigSummaryData]])
@external_api
async def read_all_config(
    request: Request,
    context: Context,
) -> ApiResponse[list[ConfigSummaryData]] | JSONResponse:
    return await _handler(request).read_all_config(context, request)


@router.get(
    "/read_config_preference",
    response_model=ApiResponse[PreferenceConfigData],
)
@external_api
async def read_config_preference(
    request: Request,
    context: Context,
    config_id: Annotated[UUID, Query()],
) -> ApiResponse[PreferenceConfigData] | JSONResponse:
    return await _handler(request).read_preference(context, config_id, request)


@router.get("/read_config_prediction", response_model=ApiResponse[PredictionConfigData])
async def read_config_prediction(
    request: Request,
    context: Context,
    config_id: Annotated[UUID, Query()],
) -> ApiResponse[PredictionConfigData] | JSONResponse:
    """Management-only endpoint: not part of the API-key contract."""

    return await _handler(request).read_prediction(context, config_id, request)


# ── Writes ───────────────────────────────────────────────────────────────────


@router.post(
    "/update_config_personality_anchor",
    response_model=ApiResponse[PersonalityAnchorData],
)
@external_api
async def update_config_personality_anchor(
    request: Request,
    context: Context,
    body: PersonalityAnchorUpdate = Body(...),  # noqa: B008
) -> ApiResponse[PersonalityAnchorData] | JSONResponse:
    return await _handler(request).update_personality_anchor(context, body, request)


@router.post("/create_config", response_model=ApiResponse[ConfigCreateResult])
@external_api
async def create_config(
    request: Request,
    context: Context,
    body: ConfigCreateBody,
) -> ApiResponse[ConfigCreateResult] | JSONResponse:
    return await _handler(request).create_config(context, body, request)


@router.api_route(
    "/update_config",
    methods=["PUT", "POST"],
    response_model=ApiResponse[ConfigUpdateResult],
)
@external_api
async def update_config(
    request: Request,
    context: Context,
    body: ConfigUpdateBody,
) -> ApiResponse[ConfigUpdateResult] | JSONResponse:
    return await _handler(request).update_config(context, body, request)


@router.api_route(
    "/update_config_extracted",
    methods=["PUT", "POST"],
    response_model=ApiResponse[ConfigUpdateResult],
)
@external_api
async def update_config_extracted(
    request: Request,
    context: Context,
    body: ExtractedUpdateBody,
) -> ApiResponse[ConfigUpdateResult] | JSONResponse:
    return await _handler(request).update_config_extracted(context, body, request)


@router.api_route(
    "/update_config_forgetting",
    methods=["PUT", "POST"],
    response_model=ApiResponse[ForgettingConfigData],
)
@external_api
async def update_config_forgetting(
    request: Request,
    context: Context,
    body: ForgettingUpdateBody,
) -> ApiResponse[ForgettingConfigData] | JSONResponse:
    return await _handler(request).update_config_forgetting(context, body, request)


@router.api_route(
    "/update_config_emotion",
    methods=["PUT", "POST"],
    response_model=ApiResponse[EmotionConfigData],
)
@external_api
async def update_config_emotion(
    request: Request,
    context: Context,
    body: EmotionUpdateBody,
) -> ApiResponse[EmotionConfigData] | JSONResponse:
    return await _handler(request).update_config_emotion(context, body, request)


@router.api_route(
    "/update_config_reflection",
    methods=["PUT", "POST"],
    response_model=ApiResponse[ReflectionMutationData],
)
@external_api
async def update_config_reflection(
    request: Request,
    context: Context,
    body: ReflectionUpdateBody,
) -> ApiResponse[ReflectionMutationData] | JSONResponse:
    return await _handler(request).update_config_reflection(context, body, request)


@router.delete("/delete_config", response_model=ApiResponse[DeleteConfigResult])
@external_api
async def delete_config(
    request: Request,
    context: Context,
    config_id: str = Query(..., description="config_id"),
) -> ApiResponse[DeleteConfigResult] | JSONResponse:
    return await _handler(request).delete_config(context, config_id, request)


@router.api_route(
    "/update_config_scene",
    methods=["PUT", "POST"],
    response_model=ApiResponse[SceneConfigData],
)
@external_api
async def update_config_scene(
    request: Request,
    context: Context,
    body: SceneUpdateBody,
) -> ApiResponse[SceneConfigData] | JSONResponse:
    return await _handler(request).update_config_scene(context, body, request)


@router.api_route(
    "/update_config_scene_community",
    methods=["PUT", "POST"],
    response_model=ApiResponse[SceneCommunityConfigData],
)
@external_api
async def update_config_scene_community(
    request: Request,
    context: Context,
    body: SceneCommunityUpdateBody,
) -> ApiResponse[SceneCommunityConfigData] | JSONResponse:
    return await _handler(request).update_config_scene_community(context, body, request)


@router.api_route(
    "/update_config_preference",
    methods=["PUT", "POST"],
    response_model=ApiResponse[PreferenceConfigData],
)
@external_api
async def update_config_preference(
    request: Request,
    context: Context,
    body: PreferenceUpdateBody,
) -> ApiResponse[PreferenceConfigData] | JSONResponse:
    return await _handler(request).update_config_preference(context, body, request)


# ── Management-only writes ───────────────────────────────────────────────────


@router.get(
    "/validate_active_config",
    response_model=ApiResponse[ValidateConfigResult],
)
async def validate_active_config(
    request: Request,
    context: Context,
) -> ApiResponse[ValidateConfigResult] | JSONResponse:
    return await _handler(request).validate_active_config(context, request)


@router.post(
    "/update_config_prediction",
    response_model=ApiResponse[PredictionConfigData],
)
async def update_config_prediction(
    request: Request,
    context: Context,
    body: PredictionUpdateBody = Body(...),  # noqa: B008
) -> ApiResponse[PredictionConfigData] | JSONResponse:
    return await _handler(request).update_config_prediction(context, body, request)


@router.post("/active_config", response_model=ApiResponse[ActiveConfigResult])
async def active_config(
    request: Request,
    context: Context,
    config_id: Annotated[UUID, Body(embed=True)],
) -> ApiResponse[ActiveConfigResult] | JSONResponse:
    return await _handler(request).active_config(context, config_id, request)


@router.post("/scene/split-demo", response_model=ApiResponse[SceneSplitDemoResponse])
async def scene_split_demo(
    request: Request,
    context: Context,
    body: SceneSplitDemoBody = Body(...),  # noqa: B008
) -> ApiResponse[SceneSplitDemoResponse] | JSONResponse:
    return await _handler(request).scene_split_demo(context, body, request)


@community_router.get(
    "/preview-cases",
    response_model=ApiResponse[list[CommunityPreviewCaseItem]],
)
async def read_scene_community_preview_cases(
    request: Request,
    context: Context,
) -> ApiResponse[list[CommunityPreviewCaseItem]] | JSONResponse:
    return await _handler(request).preview_cases(request)


@community_router.post(
    "/preview",
    response_model=ApiResponse[CommunityPreviewResult],
)
async def preview_scene_community_config(
    request: Request,
    context: Context,
    body: CommunityPreviewBody = Body(...),  # noqa: B008
) -> ApiResponse[CommunityPreviewResult] | JSONResponse:
    return await _handler(request).preview(context, body, request)
