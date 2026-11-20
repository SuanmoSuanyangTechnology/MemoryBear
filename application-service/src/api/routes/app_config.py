"""``/v1`` app-config: one config read/write pair for every app type.

The JWT face (``/api``) splits the same object into three paths —
``/{app_id}/config`` (agent), ``/{app_id}/workflow`` (workflow /
pure_workflow) and ``/{app_id}/multi-agent`` (multi_agent). The ``/v1`` face
does not copy that shape: the caller would have to read ``GET /v1/apps/{id}``
first and then assemble a different path per type. Here it is a single pair
plus a mandatory ``type``, dispatched server-side.

Why a separate router instead of reusing the existing handlers: the gateway
rewrites ``/api/x`` and ``/v1/x`` onto the *same* internal path
(``/internal/v1/x``), so ``/v1/apps/{id}/config`` and ``/api/apps/{id}/config``
necessarily land on one handler. The console calls that handler directly with
no type parameter; letting it carry the v1 dispatch would drag console-shared
code into the v1 face. ``app-config`` is a distinct path, so the boundary is
path-level and needs no ``x-api-key-id`` sniffing.

Contract (design doc §3.1):

- ``type`` is a **query** parameter on both verbs — the body shape itself
  varies by type, so the caller must know the type before deserialising.
- ``type`` must **equal** the app's stored type; a mismatch raises
  ``INVALID_PARAMETER`` rather than reinterpreting the body. ``workflow`` and
  ``pure_workflow`` share one config structure but still require their exact
  value, because ``GET /v1/apps/{app_id}`` already reports it.
- ``PUT`` is whole-replacement (same as the JWT face).
- ``multi_agent`` without a stored config returns the default template, not 404.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy.orm import Session

from ...errors import BizCode, BusinessException
from ...guards.workspace_access import require_workspace_access
from ...models.app_model import AppType
from ...schemas import app_schema, workflow_schema
from ...schemas.multi_agent_schema import MultiAgentConfigSchema, MultiAgentConfigUpdate
from ...services.agent_config_helper import enrich_agent_config
from ...services.app_service import AppService
from ...services.multi_agent_service import MultiAgentService, default_multi_agent_config
from ...services.workflow_service import WorkflowService
from ..dependencies import Principal, SyncRedis, get_principal, get_sync_db, get_sync_redis
from ..schemas.common import success
from ..schemas.response_schema import ApiResponse

router = APIRouter(prefix="/apps", tags=["App Config"])

logger = logging.getLogger(__name__)

DbSession = Annotated[Session, Depends(get_sync_db)]
Caller = Annotated[Principal, Depends(get_principal)]
WorkspaceId = Annotated[UUID, Depends(require_workspace_access)]
CacheClient = Annotated[SyncRedis, Depends(get_sync_redis)]

ConfigType = Literal["agent", "workflow", "pure_workflow", "multi_agent"]
CONFIG_TYPES = [t.value for t in AppType]

# Annotated form (not `Query(...)` in the default) so the module stays clean under
# the repo's ruff `B` rules. `type` is mandatory and must equal `app.type`.
ConfigTypeQuery = Annotated[
    ConfigType,
    Query(
        description=(
            "应用类型，四选一，必须等于该应用的 type："
            "agent | workflow | pure_workflow | multi_agent"
        ),
    ),
]
ConfigPayload = Annotated[
    dict[str, Any],
    Body(
        description=(
            "配置内容，结构随 type 不同：agent 对应 AgentConfigUpdate，"
            "workflow / pure_workflow 对应 WorkflowConfigUpdate，"
            "multi_agent 对应 MultiAgentConfigUpdate。整替换语义。"
        ),
    ),
]


def _require_type(app, requested: str) -> None:
    """Reject a declared type that differs from the stored one.

    A mismatch is a caller error, not a hint to reinterpret the body: reporting
    ``INVALID_PARAMETER`` keeps the per-type response shapes unambiguous.
    """
    if requested != app.type:
        raise BusinessException(
            f"type 与应用的类型不一致：应用类型为 {app.type}，请求传入 {requested}",
            BizCode.INVALID_PARAMETER,
            data={"reason": "APP_TYPE_MISMATCH", "app_type": app.type},
        )


@router.get(
    "/{app_id}/app-config",
    summary="获取应用配置（按 type 分派）",
    response_model=ApiResponse,
)
def get_app_config(
    app_id: UUID,
    workspace_id: WorkspaceId,
    db: DbSession,
    type: ConfigTypeQuery,
):
    """Read the config of one app, dispatched by the declared type.

    - ``agent`` → model parameters / prompt / knowledge / tools / skills
    - ``workflow`` / ``pure_workflow`` → nodes / variables / edges
    - ``multi_agent`` → orchestration mode / sub agents / routing / supervisor

    A missing config yields the default template for every branch, never 404.
    """

    service = AppService(db)
    app = service._get_app_or_404(app_id)
    service._validate_app_accessible(app, workspace_id)
    _require_type(app, type)

    if type == AppType.MULTI_AGENT:
        config = MultiAgentService(db).get_multi_agent_configs(app_id)
        if not config:
            return success(
                data=default_multi_agent_config(app_id),
                msg="该应用暂无配置，返回默认模板",
            )
        return success(data=config)

    if type == AppType.AGENT:
        cfg = service.get_agent_config(app_id=app_id, workspace_id=workspace_id)
        return success(data=app_schema.AgentConfig.model_validate(enrich_agent_config(cfg)))

    cfg = service.get_workflow_config(app_id=app_id, workspace_id=workspace_id)
    return success(data=workflow_schema.WorkflowConfig.model_validate(cfg))


@router.put(
    "/{app_id}/app-config",
    summary="更新应用配置（按 type 分派）",
    response_model=ApiResponse,
)
def update_app_config(
    app_id: UUID,
    workspace_id: WorkspaceId,
    db: DbSession,
    cache: CacheClient,
    type: ConfigTypeQuery,
    payload: ConfigPayload,
):
    """Whole-replacement config write, dispatched by the declared type.

    The body arrives as a raw mapping because the schema depends on ``type``;
    each branch validates it with the same pydantic model the JWT face uses, so
    a malformed payload yields the usual 422 envelope.
    """

    service = AppService(db)
    app = service._get_app_or_404(app_id)
    service._validate_app_writable(app, workspace_id)
    _require_type(app, type)

    if type == AppType.MULTI_AGENT:
        data = MultiAgentConfigUpdate.model_validate(payload)
        multi_agent_service = MultiAgentService(db)
        updated = multi_agent_service.update_config(app_id, data)
        config = MultiAgentConfigSchema.model_validate(updated)
        config.sub_agents = multi_agent_service.describe_sub_agents(config.sub_agents)
        return success(
            data=config,
            msg="多 Agent 配置更新成功",
        )

    if type == AppType.AGENT:
        data = app_schema.AgentConfigUpdate.model_validate(payload)
        cfg = service.update_agent_config(app_id=app_id, data=data, workspace_id=workspace_id)
        return success(
            data=app_schema.AgentConfig.model_validate(enrich_agent_config(cfg)),
            msg="更新 Agent 配置成功",
        )

    data = workflow_schema.WorkflowConfigUpdate.model_validate(payload)
    # Variable file references carry a default that only the resolver can read;
    # the same two steps run on the JWT face, so the resolved value must be
    # patched back before the write and the runtime cache invalidated after it.
    if data.variables:
        resolved = WorkflowService(db)._resolve_variables_file_defaults_sync(
            [v.model_dump() for v in data.variables]
        )
        for var_def, resolved_def in zip(data.variables, resolved, strict=True):
            var_def.default = resolved_def.get("default", var_def.default)
    cfg = service.update_workflow_config(
        app_id=app_id, data=data, workspace_id=workspace_id, redis_client=cache
    )
    return success(
        data=workflow_schema.WorkflowConfig.model_validate(cfg),
        msg="更新工作流配置成功",
    )


__all__ = ["router"]