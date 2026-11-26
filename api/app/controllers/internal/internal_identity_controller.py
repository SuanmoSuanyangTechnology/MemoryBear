"""身份服务内部回调控制器

供拆分出的身份服务（identity-service）在共存期回调老单体执行工作空间模型
配置的读写，保持宿主侧单事务与记忆副作用不变：identity 承接公网鉴权与响应
组装，写单元（workspaces 模型列 + memory_reembed_jobs）仍由老单体原子执行。

认证方式: 专用回调共享密钥（X-Callback-Secret 头，值来自环境变量
IDENTITY_CALLBACK_SECRET，两端配置同一值）。回调通道无用户凭据，用户与工作
空间由调用方经显式头传入；写路径按 X-User-Id 装载用户主体并在本侧重跑管理
员判权（不信任调用方结论）。

路由前缀: /internal/identity
最终路径: /api/internal/identity/...

端点:
    GET /workspace-reembed/active   当前工作空间在途的存量向量重算任务 id
    PUT /workspace-models           工作空间模型配置写入（宿主原子路径）
"""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Header, Query

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.identity_callback_auth import require_identity_callback_secret
from app.core.language_utils import get_language_from_header
from app.core.logging_config import get_business_logger
from app.core.response_utils import success
from app.db import get_async_db_context, get_db_context
from app.dependencies import CurrentUserSnapshot
from app.i18n import t
from app.models.user_model import User
from app.schemas.workspace_schema import WorkspaceModelsResponse, WorkspaceModelsUpdate
from app.services import memory_reembed_service, workspace_service

router = APIRouter(prefix="/internal/identity", tags=["internal-identity"])
logger = get_business_logger()


def _parse_uuid_header(raw: Optional[str], field_name: str) -> uuid.UUID:
    if not raw:
        raise BusinessException(
            f"{field_name} header is required", BizCode.INVALID_PARAMETER
        )
    try:
        return uuid.UUID(raw)
    except (TypeError, ValueError) as exc:
        raise BusinessException(
            f"Invalid {field_name} header", BizCode.INVALID_PARAMETER
        ) from exc


@router.get("/workspace-reembed/active")
def get_active_workspace_reembed_job(
    workspace_id: uuid.UUID = Query(..., description="目标工作空间 id"),
    _guard: None = Depends(require_identity_callback_secret),
):
    """当前工作空间在途（pending/running）的存量向量重算任务 id。"""

    logger.info(f"Internal identity callback - active reembed job - workspace: {workspace_id}")
    with get_db_context() as db:
        job_id = memory_reembed_service.get_active_reembed_job_id(db, workspace_id)
    return success(data={"job_id": job_id})


@router.put("/workspace-models")
async def update_workspace_models(
    models_update: WorkspaceModelsUpdate,
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-Id"),
    x_workspace_id: Optional[str] = Header(None, alias="X-Workspace-Id"),
    x_language_type: Optional[str] = Header(None, alias="X-Language-Type"),
    _guard: None = Depends(require_identity_callback_secret),
):
    """工作空间模型配置写入；代码路径与公网 PUT 完全相同（宿主原子事务）。"""

    user_id = _parse_uuid_header(x_user_id, "X-User-Id")
    tenant_id = _parse_uuid_header(x_tenant_id, "X-Tenant-Id")
    workspace_id = _parse_uuid_header(x_workspace_id, "X-Workspace-Id")
    locale = get_language_from_header(x_language_type)
    logger.info(
        f"Internal identity callback - update workspace models - "
        f"workspace: {workspace_id}, user: {user_id}"
    )

    async with get_async_db_context() as db:
        user = await db.get(User, user_id)
        if user is None or not user.is_active:
            raise BusinessException("User does not exist", BizCode.UNAUTHORIZED)
        if user.tenant_id != tenant_id:
            raise BusinessException(
                "Tenant mismatch between caller and user", BizCode.FORBIDDEN
            )
        snapshot = CurrentUserSnapshot(
            id=user.id,
            username=user.username,
            email=user.email,
            is_active=user.is_active,
            is_superuser=user.is_superuser,
            current_workspace_id=workspace_id,
            tenant_id=user.tenant_id,
        )
        updated = await workspace_service.update_workspace_models_configs(
            db=db,
            workspace_id=workspace_id,
            models_update=models_update,
            user=snapshot,
            locale=locale,
        )

    data = WorkspaceModelsResponse.model_validate(updated)
    return success(
        data={"workspace": data.model_dump()},
        msg=t("workspace.models.config_updated", locale=locale),
    )
