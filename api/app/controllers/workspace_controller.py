import uuid
from typing import List, Optional, Callable

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging_config import get_api_logger
from app.core.response_utils import success
from app.db import get_async_db, get_db
from app.dependencies import (
    CurrentUserSnapshot,
    cur_workspace_access_guard,
    cur_workspace_access_guard_async,
    get_current_superuser,
    get_current_tenant,
    get_current_user,
    get_current_user_async,
    workspace_access_guard,
)
from app.i18n.dependencies import get_current_language, get_translator
from app.i18n.serializers import (
    WorkspaceSerializer,
    WorkspaceMemberSerializer,
    WorkspaceInviteSerializer
)
from app.models.tenant_model import Tenants
from app.models.user_model import User
from app.models.workspace_model import InviteStatus
from app.schemas.response_schema import ApiResponse
from app.schemas.workspace_schema import (
    MemoryReembedEndUserListResponse,
    MemoryReembedJobResponse,
    MemoryReembedRetryResponse,
    WorkspaceCreate,
    WorkspaceDefaultModelPresetResponse,
    WorkspaceInviteCreate,
    WorkspaceMemberItem,
    WorkspaceModelOptionsResponse,
    WorkspaceMemberUpdate,
    WorkspaceModelsConfig,
    WorkspaceModelsResponse,
    WorkspaceModelsValidationResponse,
    WorkspaceModelsUpdate,
    WorkspaceRetentionPolicyResponse,
    WorkspaceRetentionPolicyUpdate,
    WorkspaceResponse,
    WorkspaceUpdate,
)
from app.services import workspace_service
from app.services import memory_reembed_service
from app.core.quota_stub import check_workspace_quota

# 获取API专用日志器
api_logger = get_api_logger()
# 需要认证的路由器
router = APIRouter(
    prefix="/workspaces",
    tags=["Workspaces"],
    dependencies=[Depends(get_current_user)]
)

# 公开路由器（不需要认证）
public_router = APIRouter(
    prefix="/workspaces",
    tags=["Workspaces"]
)


def _convert_members_to_table_items(members):
    """将工作空间成员列表转换为表格项"""
    return [
        WorkspaceMemberItem(
            id=m.id,
            username=m.user.username,
            account=m.user.email,
            role=m.role,
            last_login_at=m.user.last_login_at
        )
        for m in members
    ]


@router.get("", response_model=ApiResponse)
def get_workspaces(
    include_current: bool = Query(True, description="是否包含当前工作空间"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenants = Depends(get_current_tenant),
    language: str = Depends(get_current_language),
    t: Callable = Depends(get_translator)
):
    """获取当前租户下用户参与的所有工作空间

    Args:
        include_current: 是否包含当前工作空间（默认 True）
    """
    api_logger.info(
        f"用户 {current_user.username} 在租户 {current_tenant.name} 中请求获取工作空间列表",
        extra={"include_current": include_current}
    )

    workspaces = workspace_service.get_user_workspaces(db, current_user)

    # 如果不包含当前工作空间，则过滤掉
    if not include_current and current_user.current_workspace_id:
        workspaces = [w for w in workspaces if w.id != current_user.current_workspace_id]
        api_logger.debug(
            "过滤掉当前工作空间",
            extra={"current_workspace_id": str(current_user.current_workspace_id)}
        )

    api_logger.info(f"成功获取 {len(workspaces)} 个工作空间")
    
    # 使用序列化器添加国际化字段
    serializer = WorkspaceSerializer()
    workspaces_data = [WorkspaceResponse.model_validate(w).model_dump() for w in workspaces]
    workspaces_i18n = serializer.serialize_list(workspaces_data, language)
    
    return success(data=workspaces_i18n, msg=t("workspace.list_retrieved"))


@router.post("", response_model=ApiResponse)
@check_workspace_quota
async def create_workspace(
    workspace: WorkspaceCreate,
    language_type: str = Header(default="zh", alias="X-Language-Type"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_superuser),
    language: str = Depends(get_current_language),
    t: Callable = Depends(get_translator)
):
    """创建新的工作空间"""
    from app.core.language_utils import get_language_from_header
    
    # 验证并获取语言参数
    language = get_language_from_header(language_type)
    
    api_logger.info(
        f"用户 {current_user.username} 请求创建工作空间: {workspace.name}, "
        f"language={language}"
    )

    result = await workspace_service.create_workspace(
        db=db, workspace=workspace, user=current_user, language=language
    )

    api_logger.info(
        f"工作空间创建成功 - 名称: {workspace.name}, ID: {result.id}, "
        f"创建者: {current_user.username}, language={language}"
    )
    
    # 使用序列化器添加国际化字段
    serializer = WorkspaceSerializer()
    result_data = WorkspaceResponse.model_validate(result).model_dump()
    result_i18n = serializer.serialize(result_data, language)
    
    return success(data=result_i18n, msg=t("workspace.created"))

@router.put("", response_model=ApiResponse)
@cur_workspace_access_guard()
def update_workspace(
    workspace: WorkspaceUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    language: str = Depends(get_current_language),
    t: Callable = Depends(get_translator)
):
    """更新工作空间"""
    workspace_id = current_user.current_workspace_id
    api_logger.info(f"用户 {current_user.username} 请求更新工作空间 ID: {workspace_id}")

    result = workspace_service.update_workspace(
        db=db,
        workspace_id=workspace_id,
        workspace_in=workspace,
        user=current_user,
    )
    api_logger.info(f"工作空间更新成功 - ID: {workspace_id}, 用户: {current_user.username}")
    
    # 使用序列化器添加国际化字段
    serializer = WorkspaceSerializer()
    result_data = WorkspaceResponse.model_validate(result).model_dump()
    result_i18n = serializer.serialize(result_data, language)
    
    return success(data=result_i18n, msg=t("workspace.updated"))


@router.get("/temporary-memory-retention", response_model=ApiResponse)
@cur_workspace_access_guard()
def get_workspace_retention_policy(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    t: Callable = Depends(get_translator),
):
    """获取当前工作空间的临时身份保留策略。"""
    workspace_id = current_user.current_workspace_id
    api_logger.info(
        f"用户 {current_user.username} 请求获取工作空间 {workspace_id} 的保留策略"
    )
    retention_days, end_user_count = workspace_service.get_workspace_retention_policy(
        db=db,
        workspace_id=workspace_id,
        user=current_user,
    )
    return success(
        data=WorkspaceRetentionPolicyResponse(
            retention_days=retention_days,
            end_user_count=end_user_count,
        ).model_dump(),
        msg=t("workspace.retention_policy.retrieved"),
    )


@router.put("/temporary-memory-retention", response_model=ApiResponse)
@cur_workspace_access_guard()
def update_workspace_retention_policy(
    policy: WorkspaceRetentionPolicyUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    t: Callable = Depends(get_translator),
):
    """更新当前工作空间的临时身份保留策略。"""
    workspace_id = current_user.current_workspace_id
    api_logger.info(
        f"用户 {current_user.username} 请求更新工作空间 {workspace_id} 的保留策略"
    )
    retention_days = workspace_service.update_workspace_retention_policy(
        db=db,
        workspace_id=workspace_id,
        retention_days=policy.retention_days,
        user=current_user,
    )
    return success(
        data=WorkspaceRetentionPolicyResponse(
            retention_days=retention_days,
        ).model_dump(exclude_unset=True),
        msg=t("workspace.retention_policy.updated"),
    )


@router.get("/members", response_model=ApiResponse)
@cur_workspace_access_guard()
def get_cur_workspace_members(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    language: str = Depends(get_current_language),
    t: Callable = Depends(get_translator)
):
    """获取工作空间成员列表（关系序列化）"""
    api_logger.info(f"用户 {current_user.username} 请求获取工作空间 {current_user.current_workspace_id} 的成员列表")

    members = workspace_service.get_workspace_members(
        db=db,
        workspace_id=current_user.current_workspace_id,
        user=current_user,
    )
    api_logger.info(f"工作空间成员列表获取成功 - ID: {current_user.current_workspace_id}, 数量: {len(members)}")
    
    # 转换为表格项并使用序列化器添加国际化字段
    table_items = _convert_members_to_table_items(members)
    serializer = WorkspaceMemberSerializer()
    members_data = [item.model_dump() for item in table_items]
    members_i18n = serializer.serialize_list(members_data, language)
    
    return success(data=members_i18n, msg=t("workspace.members.list_retrieved"))


@router.put("/members", response_model=ApiResponse)
@cur_workspace_access_guard()
def update_workspace_members(

    updates: List[WorkspaceMemberUpdate],
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    t: Callable = Depends(get_translator)
):
    workspace_id = current_user.current_workspace_id
    api_logger.info(f"用户 {current_user.username} 请求更新工作空间 {workspace_id} 的成员角色")
    members = workspace_service.update_workspace_member_roles(
        db=db,
        workspace_id=workspace_id,
        updates=updates,
        user=current_user,
    )
    api_logger.info(f"工作空间成员角色更新成功 - ID: {workspace_id}, 数量: {len(members)}")
    return success(msg=t("workspace.members.role_updated"))


@router.delete("/members/{member_id}", response_model=ApiResponse)
@cur_workspace_access_guard()
async def delete_workspace_member(
    member_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    t: Callable = Depends(get_translator)
):
    workspace_id = current_user.current_workspace_id
    api_logger.info(f"用户 {current_user.username} 请求删除工作空间 {workspace_id} 的成员 {member_id}")

    await workspace_service.delete_workspace_member(
        db=db,
        workspace_id=workspace_id,
        member_id=member_id,
        user=current_user,
    )
    api_logger.info(f"工作空间成员删除成功 - ID: {workspace_id}, 成员: {member_id}")
    return success(msg=t("workspace.members.deleted"))


# 创建空间协作邀请
@router.post("/invites", response_model=ApiResponse)
@cur_workspace_access_guard()
def create_workspace_invite(
    invite_data: WorkspaceInviteCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    language: str = Depends(get_current_language),
    t: Callable = Depends(get_translator)
):
    """创建工作空间邀请"""
    workspace_id = current_user.current_workspace_id
    api_logger.info(f"用户 {current_user.username} 请求为工作空间 {workspace_id} 创建邀请: {invite_data.email}")

    result = workspace_service.create_workspace_invite(
        db=db,
        workspace_id=workspace_id,
        invite_data=invite_data,
        user=current_user
    )
    api_logger.info(f"工作空间邀请创建成功 - 工作空间: {workspace_id}, 邮箱: {invite_data.email}")
    
    # 使用序列化器添加国际化字段
    serializer = WorkspaceInviteSerializer()
    result_i18n = serializer.serialize(result, language)
    
    return success(data=result_i18n, msg=t("workspace.invites.created"))


@router.get("/invites", response_model=ApiResponse)
@cur_workspace_access_guard()
def get_workspace_invites(

    status_filter: Optional[InviteStatus] = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    language: str = Depends(get_current_language),
    t: Callable = Depends(get_translator)
):
    """获取工作空间邀请列表"""
    workspace_id = current_user.current_workspace_id
    api_logger.info(f"用户 {current_user.username} 请求获取工作空间 {workspace_id} 的邀请列表")

    invites = workspace_service.get_workspace_invites(
        db=db,
        workspace_id=workspace_id,
        user=current_user,
        status=status_filter,
        limit=limit,
        offset=offset
    )
    api_logger.info(f"成功获取 {len(invites)} 个邀请记录")
    
    # 使用序列化器添加国际化字段
    serializer = WorkspaceInviteSerializer()
    invites_i18n = serializer.serialize_list(invites, language)
    
    return success(data=invites_i18n, msg=t("workspace.invites.list_retrieved"))


@public_router.get("/invites/validate/{token}", response_model=ApiResponse)
def get_workspace_invite_info(
    token: str,
    db: Session = Depends(get_db),
    language: str = Depends(get_current_language),
    t: Callable = Depends(get_translator)
):
    """获取工作空间邀请用户信息（无需认证）"""
    result = workspace_service.validate_invite_token(db=db, token=token)
    api_logger.info(f"工作空间邀请验证成功 - 邀请: {token}")
    
    # 使用序列化器添加国际化字段
    serializer = WorkspaceInviteSerializer()
    result_i18n = serializer.serialize(result, language)
    
    return success(data=result_i18n, msg=t("workspace.invites.validated"))


@router.delete("/invites/{invite_id}", response_model=ApiResponse)
@cur_workspace_access_guard()
def revoke_workspace_invite(

    invite_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    language: str = Depends(get_current_language),
    t: Callable = Depends(get_translator)
):
    """撤销工作空间邀请"""
    workspace_id = current_user.current_workspace_id
    api_logger.info(f"用户 {current_user.username} 请求撤销工作空间 {workspace_id} 的邀请 {invite_id}")

    result = workspace_service.revoke_workspace_invite(
        db=db,
        workspace_id=workspace_id,
        invite_id=invite_id,
        user=current_user
    )
    api_logger.info(f"工作空间邀请撤销成功 - 邀请: {invite_id}")
    
    # 使用序列化器添加国际化字段
    serializer = WorkspaceInviteSerializer()
    result_i18n = serializer.serialize(result, language)
    
    return success(data=result_i18n, msg=t("workspace.invites.revoked"))

# ==================== 公开邀请接口（无需认证） ====================

# # 创建一个新的路由器用于公开接口
# public_router = APIRouter(
#     prefix="/invites",
#     tags=["Public Invites"]
# )

# @public_router.get("/validate", response_model=ApiResponse)
# def validate_invite_token(
#     token: str = Query(..., description="邀请令牌"),
#     db: Session = Depends(get_db),
# ):
#     """验证邀请令牌（公开接口）"""
#     api_logger.info(f"验证邀请令牌请求")
@router.put("/{workspace_id}/switch", response_model=ApiResponse)
@workspace_access_guard()
def switch_workspace(
    workspace_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    t: Callable = Depends(get_translator)
):
    """切换工作空间"""
    api_logger.info(f"用户 {current_user.username} 请求切换工作空间为 {workspace_id}")

    workspace_service.switch_workspace(
        db=db,
        workspace_id=workspace_id,
        user=current_user,
    )
    api_logger.info(f"成功切换工作空间为 {workspace_id}")
    return success(msg=t("workspace.switched"))


@router.get("/storage", response_model=ApiResponse)
@cur_workspace_access_guard_async()
async def get_workspace_storage_type(
        current_user: CurrentUserSnapshot = Depends(get_current_user_async),
        t: Callable = Depends(get_translator)
):
    """获取当前工作空间的存储类型（纯异步版本）"""
    from app.db import get_async_db_context

    workspace_id = current_user.current_workspace_id
    api_logger.info(f"用户 {current_user.username} 请求获取工作空间 {workspace_id} 的存储类型")

    async with get_async_db_context() as async_db:
        storage_type = await workspace_service.get_workspace_storage_type_async(
            db=async_db, workspace_id=workspace_id, user=current_user
        )
    api_logger.info(f"成功获取工作空间 {workspace_id} 的存储类型: {storage_type}")
    return success(data={"storage_type": storage_type}, msg=t("workspace.storage.type_retrieved"))


@router.get("/default_models", response_model=ApiResponse)
def get_default_workspace_models(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    api_logger.info(f"用户 {current_user.username} 请求获取默认工作空间模型配置")
    data = workspace_service.get_default_workspace_models(db, allow_empty=True)
    if not data:
        return success(data={})
    return success(data=WorkspaceDefaultModelPresetResponse.model_validate(data))


@router.get("/model_options", response_model=ApiResponse)
def get_workspace_model_options(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenants = Depends(get_current_tenant),
):
    api_logger.info(f"用户 {current_user.username} 请求获取工作空间模型候选列表")
    data = workspace_service.get_workspace_model_options(db, current_tenant.id)
    return success(data=WorkspaceModelOptionsResponse.model_validate(data))


@router.get("/workspace_models", response_model=ApiResponse)
@cur_workspace_access_guard()
def workspace_models_configs(
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
        language: str = Depends(get_current_language),
        t: Callable = Depends(get_translator)
):
    """获取当前工作空间的模型配置（llm, embedding, rerank）"""
    workspace_id = current_user.current_workspace_id
    api_logger.info(f"用户 {current_user.username} 请求获取工作空间 {workspace_id} 的模型配置")

    configs = workspace_service.get_workspace_models_configs(
        db=db,
        workspace_id=workspace_id,
        user=current_user,
        locale=language,
    )

    if configs is None:
        api_logger.warning(f"工作空间 {workspace_id} 不存在或无权访问")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=t("workspace.not_found")
        )

    api_logger.info(
        f"成功获取工作空间 {workspace_id} 的模型配置: "
        f"llm={configs.get('llm')}, embedding={configs.get('embedding')}, "
        f"rerank={configs.get('rerank')}, reembed_job_id={configs.get('reembed_job_id')}"
    )
    return success(data=WorkspaceModelsResponse.model_validate(configs), msg=t("workspace.models.config_retrieved"))


@router.post("/workspace_models/validate", response_model=ApiResponse)
@cur_workspace_access_guard()
async def validate_workspace_models_configs(
        models_update: WorkspaceModelsUpdate | None = None,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
        language: str = Depends(get_current_language),
        t: Callable = Depends(get_translator)
):
    """校验当前工作空间模型配置"""
    from app.core.language_utils import get_language_from_header

    workspace_id = current_user.current_workspace_id
    locale = get_language_from_header(language)
    result = await workspace_service.validate_workspace_models_configs(
        db=db,
        workspace_id=workspace_id,
        user=current_user,
        locale=locale,
        models_update=models_update,
    )
    return success(data=WorkspaceModelsValidationResponse.model_validate(result), msg=t("workspace.models.config_verified"))


@router.put("/workspace_models", response_model=ApiResponse)
@cur_workspace_access_guard_async()
async def update_workspace_models_configs(
        models_update: WorkspaceModelsUpdate,
        db: AsyncSession = Depends(get_async_db),
        current_user: CurrentUserSnapshot = Depends(get_current_user_async),
        language: str = Depends(get_current_language),
        t: Callable = Depends(get_translator)
):
    """更新当前工作空间的模型配置，并校验模型可用性"""
    from app.core.language_utils import get_language_from_header

    workspace_id = current_user.current_workspace_id
    locale = get_language_from_header(language)
    api_logger.info(f"用户 {current_user.username} 请求更新工作空间 {workspace_id} 的模型配置")

    updated_workspace = await workspace_service.update_workspace_models_configs(
        db=db,
        workspace_id=workspace_id,
        models_update=models_update,
        user=current_user,
        locale=locale,
    )

    api_logger.info(
        f"成功更新工作空间 {workspace_id} 的模型配置: "
        f"llm={updated_workspace.get('llm')}, embedding={updated_workspace.get('embedding')}, "
        f"rerank={updated_workspace.get('rerank')}, "
        f"reembed_job_id={updated_workspace.get('reembed_job_id')}"
    )

    data = WorkspaceModelsResponse.model_validate(updated_workspace)
    return success(data={"workspace": data.model_dump()}, msg=t("workspace.models.config_updated"))


@router.get("/workspace_reembed/current", response_model=ApiResponse)
@cur_workspace_access_guard_async()
async def get_current_workspace_reembed_job(
        db: AsyncSession = Depends(get_async_db),
        current_user: CurrentUserSnapshot = Depends(get_current_user_async),
):
    """查询当前工作空间**近 24h 内最新的一次**存量向量重算任务。

    embedding 底层模型变更后前端靠它拿 job_id 并轮询进度。

    成功的那次照常返回——它正是用户当下要看的"最近一次重算"（完成通知就在那一刻
    发的）。退场只由时间决定：终态任务结束满 24h 后回到"无任务"（语义见
    ``memory_reembed_service._current_job_row_is_expired``），因此这个端点仍不是
    "永远有任务可看"，调用方不能假设一定拿得到 job_id。

    在途任务不受 24h 限制：它还在跑，藏掉只会让轮询方以为没任务了。过期任务要拿
    详情，用 ``GET /workspace_reembed/{job_id}``（按 id 查询不看新旧）。
    """
    job = await memory_reembed_service.get_current_reembed_job_async(
        db,
        current_user.current_workspace_id,
    )
    if job is None:
        return success(data=None, msg="no re-embed job")
    payload = await memory_reembed_service.build_reembed_job_payload(db, job)
    return success(data=MemoryReembedJobResponse.model_validate(payload))


@router.get("/workspace_reembed/{job_id}", response_model=ApiResponse)
@cur_workspace_access_guard_async()
async def get_workspace_reembed_job(
        job_id: uuid.UUID,
        db: AsyncSession = Depends(get_async_db),
        current_user: CurrentUserSnapshot = Depends(get_current_user_async),
):
    """查询指定的存量向量重算任务（限当前工作空间）。"""
    job = await memory_reembed_service.get_reembed_job_async(db, job_id)
    if job is None or job.workspace_id != current_user.current_workspace_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="reembed job not found",
        )
    payload = await memory_reembed_service.build_reembed_job_payload(db, job)
    return success(data=MemoryReembedJobResponse.model_validate(payload))


def _require_workspace_admin(
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
) -> None:
    """重试类接口的管理员门禁（重试会触发真实重算，与切换模型同权限）。"""
    workspace_service.require_workspace_admin(
        db, current_user.current_workspace_id, current_user
    )


def _reembed_end_user_page(
        job,
        *,
        status: str | None,
        page: int,
        pagesize: int,
        db: Session,
) -> MemoryReembedEndUserListResponse:
    payload = memory_reembed_service.list_job_end_users(
        db,
        job_id=job.id,
        status=status,
        page=page,
        pagesize=pagesize,
    )
    return MemoryReembedEndUserListResponse.model_validate(payload)


# 注意：本路由必须注册在 ``/workspace_reembed/{job_id}/end_users`` 之前，
# 否则 "current" 会被当作 job_id 去做 UUID 校验而报 422。
@router.get("/workspace_reembed/current/end_users", response_model=ApiResponse)
@cur_workspace_access_guard()
def list_current_workspace_reembed_end_users(
        status: str | None = Query(None, description="queued/running/succeeded/failed"),
        page: int = Query(1, ge=1),
        pagesize: int = Query(20, ge=1, le=200),
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
):
    """查询当前工作空间近 24h 内最新一次重算任务下，各 end_user 的重算状态。

    与 ``GET /workspace_reembed/current`` 同一判据：终态任务结束满 24h 后回到
    "无任务"（成功的那次同样按时间退场，不提前清空）。

    但**空态形状不同**：``/current`` 是详情接口，无任务返回 ``data: {}``；
    本接口是分页接口，无任务返回空分页信封（``job_id: null`` + 空的
    ``page``/``items`` + 四键全 0 的 ``summary``），见
    :func:`memory_reembed_service.empty_job_end_user_page`。分页接口的调用方
    不该为"有没有任务"写两套解析。
    """
    job = memory_reembed_service.get_current_reembed_job(
        db, current_user.current_workspace_id
    )
    if job is None:
        return success(
            data=MemoryReembedEndUserListResponse.model_validate(
                memory_reembed_service.empty_job_end_user_page(
                    page=page, pagesize=pagesize
                )
            ),
            msg="no re-embed job",
        )
    return success(
        data=_reembed_end_user_page(
            job, status=status, page=page, pagesize=pagesize, db=db
        )
    )


def _current_reembed_job_or_none(
        db: Session,
        workspace_id: uuid.UUID,
):
    """把 ``current`` 解析成任务行；没有返回 ``None``（**不报错**）。

    "没有当前任务"（从未重算，或最新那一条已过期超过 24h）不是失败：调用方只是点了
    个按钮，而那时没有任何行需要重试。返回 404 会让前端把它当错误弹出来，而
    ``retry_job_users`` 早把"有任务但没有终态失败行"定成 ``retried: 0`` 的非错误
    语义——两种"没重试任何行"不该一个报错一个不报。空结果由
    :func:`memory_reembed_service.empty_job_retry_result` 给出。

    解析到的可能是已成功的任务（成功与否不再影响可见性）：``finalize_job_if_complete``
    只在没有终态失败行时才写 ``succeeded``，所以这种任务必然没有可重试的行，会落到
    ``retry_job_users`` 的 ``retried: 0`` 分支，不会重开任务。

    与 ``{job_id}`` 版的分工：那是调用方**指名**的任务，不存在就仍然 404
    （见 ``list_workspace_reembed_end_users`` 等）。已过期的旧任务只走那条路——
    它们在 ``/current`` 上已经不该可见了。
    """
    return memory_reembed_service.get_current_reembed_job(db, workspace_id)


# 这两个路由同样必须注册在 ``/workspace_reembed/{job_id}/end_users/...`` 之前：
# 段数相同，late 注册会让 "current" 先被 ``{job_id}`` 吃掉（UUID 校验 → 422）。
@router.post(
    "/workspace_reembed/current/end_users/retry_failed",
    response_model=ApiResponse,
)
@cur_workspace_access_guard()
def retry_failed_current_workspace_reembed_end_users(
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
        _admin: None = Depends(_require_workspace_admin),
):
    """一键重试**当前**任务下所有终态失败的 end_user。

    与 ``POST /workspace_reembed/{job_id}/end_users/retry_failed`` 同一语义与同一
    响应，只是不需要调用方持有 job_id。「当前」的判据与
    ``GET /workspace_reembed/current`` 完全同源（``_current_job_filters`` +
    ``_current_job_row_is_expired``），所以"列表看到的那次任务"就是"这里重试的那次
    任务"。

    当前任务已成功时结果为空的 ``retried: 0``：``succeeded`` 是按"没有终态失败行"
    写的，本来就没有可重试的行。任务已过期（结束满 24h）时才回到"没有当前任务"，
    同样是 ``retried: 0``。要针对某一次明确重试，用带 job_id 的那条。

    没有当前任务时返回 200 + 空结果（``retried: 0``），不是 404。
    """
    job = _current_reembed_job_or_none(db, current_user.current_workspace_id)
    if job is None:
        return success(
            data=MemoryReembedRetryResponse.model_validate(
                memory_reembed_service.empty_job_retry_result()
            ),
            msg="no re-embed job",
        )
    payload = memory_reembed_service.retry_job_users(db, job_id=job.id)
    return success(data=MemoryReembedRetryResponse.model_validate(payload))


@router.post(
    "/workspace_reembed/current/end_users/{end_user_id}/retry",
    response_model=ApiResponse,
)
@cur_workspace_access_guard()
def retry_current_workspace_reembed_end_user(
        end_user_id: str,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
        _admin: None = Depends(_require_workspace_admin),
):
    """重试**当前**任务下单个终态失败的 end_user（内部重试预算已耗尽的那种）。

    没有当前任务时同上一并返回 200 + 空结果。
    """
    job = _current_reembed_job_or_none(db, current_user.current_workspace_id)
    if job is None:
        return success(
            data=MemoryReembedRetryResponse.model_validate(
                memory_reembed_service.empty_job_retry_result()
            ),
            msg="no re-embed job",
        )
    payload = memory_reembed_service.retry_job_users(
        db, job_id=job.id, end_user_ids=[end_user_id]
    )
    return success(data=MemoryReembedRetryResponse.model_validate(payload))


@router.get("/workspace_reembed/{job_id}/end_users", response_model=ApiResponse)
@cur_workspace_access_guard()
def list_workspace_reembed_end_users(
        job_id: uuid.UUID,
        status: str | None = Query(None, description="queued/running/succeeded/failed"),
        page: int = Query(1, ge=1),
        pagesize: int = Query(20, ge=1, le=200),
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
):
    """查询指定重算任务下，各 end_user 的重算状态（限当前工作空间）。"""
    from fastapi import status as http_status
    job = memory_reembed_service.get_reembed_job(db, job_id)
    if job is None or job.workspace_id != current_user.current_workspace_id:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="reembed job not found",
        )
    return success(
        data=_reembed_end_user_page(
            job, status=status, page=page, pagesize=pagesize, db=db
        )
    )


@router.post(
    "/workspace_reembed/{job_id}/end_users/retry_failed",
    response_model=ApiResponse,
)
@cur_workspace_access_guard()
def retry_failed_workspace_reembed_end_users(
        job_id: uuid.UUID,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
        _admin: None = Depends(_require_workspace_admin),
):
    """一键重试该任务下所有终态失败的 end_user。"""
    job = memory_reembed_service.get_reembed_job(db, job_id)
    if job is None or job.workspace_id != current_user.current_workspace_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="reembed job not found",
        )
    payload = memory_reembed_service.retry_job_users(db, job_id=job_id)
    return success(data=MemoryReembedRetryResponse.model_validate(payload))


@router.post(
    "/workspace_reembed/{job_id}/end_users/{end_user_id}/retry",
    response_model=ApiResponse,
)
@cur_workspace_access_guard()
def retry_workspace_reembed_end_user(
        job_id: uuid.UUID,
        end_user_id: str,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
        _admin: None = Depends(_require_workspace_admin),
):
    """重试单个终态失败的 end_user（内部重试预算已耗尽的那种）。"""
    job = memory_reembed_service.get_reembed_job(db, job_id)
    if job is None or job.workspace_id != current_user.current_workspace_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="reembed job not found",
        )
    payload = memory_reembed_service.retry_job_users(
        db, job_id=job_id, end_user_ids=[end_user_id]
    )
    return success(data=MemoryReembedRetryResponse.model_validate(payload))
