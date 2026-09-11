import uuid
from collections.abc import Callable

from fastapi import APIRouter, Depends

from src.constants.error_codes import BizCode
from src.constants.graph_data_constants import (
    CENTER_MODE_LIMIT_HARD_MAX,
    DEPTH_HARD_MAX,
)
from src.i18n.dependencies import get_translator
from src.infrastructure.database.session import get_async_db_context
from src.infrastructure.logger.config import get_logger
from src.middleware.auth import Principal, get_principal
from src.schemas.response_schemas import ApiResponse
from src.services.analytics_service import AnalyticsService
from src.services.end_user_service import EndUserService
from src.utils.response_utils import fail, success

router = APIRouter(
    prefix="/memory/analytics",
    tags=["ontology"]
)
logger = get_logger("analytics")


@router.get("/graph_data", response_model=ApiResponse)
async def get_graph_data(
        end_user_id: uuid.UUID,
        node_types: str | None = None,
        limit: int = 100,
        depth: int = 1,
        center_node_id: str | None = None,
        principal: Principal = Depends(get_principal),
        t: Callable = Depends(get_translator),
) -> dict:
    """查询终端用户的图数据（可视化用）。

    鉴权两层：网关/中间件已验签主体（principal），此处再校验 end_user 归属——
    只凭一个 end_user_id 就能读图，越权面比按 workspace 过滤的接口更大，故必须
    确认该 end_user 属于 principal.workspace_id。
    """
    workspace_id = principal.workspace_id

    # 参数收敛：把调用方传入值夹到硬上限内（超限不报错，静默收敛，与老单体一致）
    limit = min(limit, CENTER_MODE_LIMIT_HARD_MAX)
    depth = min(depth, DEPTH_HARD_MAX)
    node_types_list = None
    if node_types:
        node_types_list = [n.strip() for n in node_types.split(",") if n.strip()]

    async with get_async_db_context() as db:
        is_user_exist = await EndUserService().verify_end_user_in_workspace(
            db,
            end_user_id=end_user_id,
            workspace_id=workspace_id,
        )

    if not is_user_exist:
        # 三种情况（不存在 / 已软删 / 不属于本 workspace）统一回同一句，不区分原因
        # ——否则可据响应差异探测出别的 workspace 里有哪些 end_user_id。
        logger.warning(
            "graph_data 拒绝: end_user 不属于该 workspace "
            "(end_user_id=%s, workspace_id=%s)", end_user_id, workspace_id
        )
        return fail(
            code=BizCode.END_USER_NOT_FOUND,
            msg=t("errors.end_user.not_found"),
        )

    data = await AnalyticsService().get_graph(
        end_user_id=end_user_id,
        limit=limit,
        depth=depth,
        node_types=node_types_list,
        center_node_id=center_node_id,
    )

    return success(data=data, msg=t("analytics.success.graph_data"))