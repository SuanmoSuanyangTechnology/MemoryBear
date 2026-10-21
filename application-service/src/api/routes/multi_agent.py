"""Multi-agent config routes.

Mirrors the monolith ``multi_agent_controller`` definition-plane endpoints:
config read, config update and the handoff routing preflight. The handoffs
chat / stream / history endpoints stay in the monolith for now.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ...guards.workspace_access import require_workspace_access
from ...schemas.multi_agent_schema import MultiAgentConfigSchema, MultiAgentConfigUpdate
from ...services.app_service import AppService
from ...services.multi_agent_handoffs import MultiAgentHandoffsService
from ...services.multi_agent_service import MultiAgentService
from ..dependencies import Principal, get_principal, get_sync_db
from ..schemas.common import success
from ..schemas.response_schema import ApiResponse

router = APIRouter(prefix="/apps", tags=["Multi-Agent"])

DbSession = Annotated[Session, Depends(get_sync_db)]
Caller = Annotated[Principal, Depends(get_principal)]
WorkspaceId = Annotated[UUID, Depends(require_workspace_access)]


@router.get(
    "/{app_id}/multi-agent",
    summary="获取当前应用的最新有效多 Agent 配置",
    response_model=ApiResponse,
)
def get_multi_agent_configs(
    app_id: UUID,
    workspace_id: WorkspaceId,
    db: DbSession,
    principal: Caller,
):
    """Get the app's latest active multi-agent config, or the default template"""
    app_service = AppService(db)
    app = app_service._get_app_or_404(app_id)
    app_service._validate_app_accessible(app, workspace_id)

    service = MultiAgentService(db)

    # Fetch the latest active config by app_id (agent_id already mapped to app_id)
    config = service.get_multi_agent_configs(app_id)

    if not config:
        # Return the default template
        default_template = {
            "app_id": str(app_id),
            "default_model_config_id": None,
            "model_parameters": None,
            "orchestration_mode": "supervisor_loop",
            "sub_agents": [],
            "routing_rules": [],
            "execution_config": {
                # Aligned with the ExecutionConfig schema defaults. The advanced guard
                # options (timeout / stream_idle_timeout / parallel_limit /
                # retry_on_failure / max_retries) have no frontend entry point and
                # take effect via the backend schema defaults instead
                "max_iterations": 10
            },
            # The supervisor is an Agent: default is the bare supervisor
            # (None = hardcoded prompt + SubAgentTool only)
            "supervisor_config": None,
            # No cluster variables declared by default (None = runtime falls back
            # to the union of sub-agent variables)
            "variables": None,
            "aggregation_strategy": "merge",
        }
        return success(
            data=default_template,
            msg="该应用暂无配置，返回默认模板"
        )

    # config is already a dict; return it directly
    return success(data=config)


@router.put(
    "/{app_id}/multi-agent",
    summary="更新多 Agent 配置",
    response_model=ApiResponse,
)
def update_multi_agent_config(
    app_id: UUID,
    data: MultiAgentConfigUpdate,
    workspace_id: WorkspaceId,
    db: DbSession,
    principal: Caller,
):
    """Update multi-agent config"""
    app_service = AppService(db)
    app = app_service._get_app_or_404(app_id)
    app_service._validate_app_writable(app, workspace_id)

    config = MultiAgentService(db).update_config(app_id, data)
    return success(
        data=MultiAgentConfigSchema.model_validate(config),
        msg="多 Agent 配置更新成功"
    )


@router.post(
    "/{app_id}/handoffs/test-routing",
    summary="测试 Handoff 路由决策",
    response_model=ApiResponse,
)
def test_handoff_routing(
    app_id: UUID,
    workspace_id: WorkspaceId,
    db: DbSession,
    principal: Caller,
    message: str = Query(..., description="测试消息"),
):
    """Test the Handoff routing decision (no actual execution)

    For debugging and testing the routing logic; returns:
    - initial_agent_id: the initially selected Agent
    - available_handoff_tools: the available handoff tool list
    - handoff_suggestion: the automatic handoff suggestion

    Does not actually run any Agent; only the routing decision is returned
    """
    app_service = AppService(db)
    app = app_service._get_app_or_404(app_id)
    app_service._validate_app_accessible(app, workspace_id)

    handoffs_service = MultiAgentHandoffsService(db, MultiAgentService(db))

    result = handoffs_service.test_handoff_routing(app_id, message)

    return success(
        data=result,
        msg="路由测试完成"
    )
