"""Build a validated graph response for both authenticated controller entries."""
import logging
from uuid import UUID

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.response_utils import success
from app.schemas.scene_community_graph_schema import ErrorResponse, GraphResponse
from app.services.scene_community_graph_service import get_scene_community_graph_service

logger = logging.getLogger(__name__)

SCENE_GRAPH_ERROR_RESPONSES = {
    **{status: {"model": ErrorResponse} for status in (400, 402, 429, 500, 503)},
    **{status: {"description": "Existing authentication/workspace error"} for status in (401, 403, 404)},
    200: {
        "model": GraphResponse | ErrorResponse,
        "description": "Check code: USER_NOT_FOUND is HTTP 200 / code 4001.",
    },
}


async def scene_community_graph_response(workspace_id: UUID | None, requested_id: str) -> dict:
    if workspace_id is None:
        raise BusinessException("请先切换到一个工作空间", BizCode.INVALID_PARAMETER)
    try:
        data = await get_scene_community_graph_service().get_graph(workspace_id, requested_id)
        # Validation happens before the API Key decorator wraps this dict in JSONResponse.
        return GraphResponse.model_validate(success(data=data)).model_dump(mode="json")
    except BusinessException:
        raise
    except Exception as exc:
        logger.error("scene_graph.unexpected_error", extra={"error_type": type(exc).__name__})
        raise BusinessException("场景社区图谱查询失败", BizCode.INTERNAL_ERROR) from exc
