"""Home-page BFF routes (gateway rewrites ``/api/home-page/*`` to here).

``statistics``/``workspaces`` require the tenant-scoped principal assembled
by the gateway. ``version`` stays anonymous, same as the monolith's
``/home-page/version`` (the dashboard renders the version card regardless of
login state; ``is_public_path`` carries the exemption).

Messages are the monolith's literal Chinese strings, passed verbatim so the
envelope matches byte for byte.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...integrations.ops_service import get_ops_service_client
from ...services.home_page_service import HomePageService
from ..dependencies import Principal, get_principal, get_sync_db
from ..schemas.common import success
from ..schemas.response_schema import ApiResponse

router = APIRouter(prefix="/home-page", tags=["Home Page"])

DbSession = Annotated[Session, Depends(get_sync_db)]
Caller = Annotated[Principal, Depends(get_principal)]


@router.get("/statistics", response_model=ApiResponse)
def get_home_statistics(principal: Caller, db: DbSession):
    """获取首页统计数据"""

    statistics = HomePageService(db).get_home_statistics(principal.tenant_id)
    return success(data=statistics.model_dump(), msg="统计数据获取成功")


@router.get("/workspaces", response_model=ApiResponse)
def get_workspace_list(principal: Caller, db: DbSession):
    """获取工作空间列表"""

    workspace_list = HomePageService(db).get_workspace_list(principal.tenant_id)
    return success(
        data=[workspace.model_dump() for workspace in workspace_list],
        msg="工作空间列表获取成功",
    )


@router.get("/version", response_model=ApiResponse)
def get_system_version():
    """获取系统版本号 + 说明"""

    payload = HomePageService.get_system_version(get_ops_service_client())
    return success(data=payload, msg="系统版本获取成功")
