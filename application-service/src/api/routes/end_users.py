"""End-user family routes (the manager face and the ``/v1`` face share one
path set after the gateway rewrite).

Wires the monolith ``end_user_controller`` (manager, JWT) and
``service/end_user_api_controller`` (API-key) endpoints onto one router: both
faces arrive as ``/internal/v1/end_user/*`` with the claims-injected identity,
and the workspace always comes from the principal. Writes that carry
memory-side effects (a merge hit on identity update, an alias change on info
update) are forwarded to the monolith ``/v1`` face; the PG-only paths run
locally.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ...errors import BizCode, BusinessException, InternalServerError
from ...infrastructure.upstream import MonolithUpstream, UpstreamUnavailableError
from ...quota.alerts import report_quota_change_sync
from ...quota.usage import require_end_user_quota
from ...repositories import cross_domain_read
from ...repositories.end_user_info_repository import EndUserInfoRepository
from ...repositories.end_user_repository import EndUserRepository
from ...schemas import end_user_info_schema, end_user_schema
from ...services.end_user_service import EndUserService
from ...utils.datetime_utils import to_timestamp_ms
from ..dependencies import Principal, get_principal, get_sync_db, get_upstream
from ..schemas.common import fail, success
from ..schemas.response_schema import ApiResponse, PageMeta

router = APIRouter(prefix="/end_user", tags=["End User"])

logger = logging.getLogger(__name__)

DbSession = Annotated[Session, Depends(get_sync_db)]
Caller = Annotated[Principal, Depends(get_principal)]
Upstream = Annotated[MonolithUpstream, Depends(get_upstream)]
EndUserQuota = Annotated[None, Depends(require_end_user_quota)]


def _forward_response(
    upstream: MonolithUpstream, path: str, payload: dict[str, Any]
) -> JSONResponse:
    """Replay a write on the monolith ``/v1`` face and pass its response back
    unchanged (the caller has no open transaction at this point)."""

    result = upstream.forward("POST", path, json_body=payload)
    return JSONResponse(status_code=result.status_code, content=result.body)


@router.get("/info", summary="查询终端用户信息", response_model=ApiResponse)
def get_end_user_info(
    end_user_id: str,
    db: DbSession,
    principal: Caller,
):
    workspace_id = principal.workspace_id
    if workspace_id is None:
        logger.warning("End-user info lookup without a workspace: actor=%s", principal.actor_id)
        return fail(
            BizCode.INVALID_PARAMETER.value,
            "请先切换到一个工作空间",
            "current_workspace_id is None",
        )

    try:
        end_user_uuid = UUID(end_user_id)
    except (ValueError, AttributeError):
        return fail(BizCode.INVALID_USER_ID.value, "无效的终端用户ID格式", "invalid uuid")

    end_user = EndUserRepository(db).get_end_user_by_id(end_user_uuid)
    if end_user is None:
        return fail(BizCode.USER_NOT_FOUND.value, "终端用户不存在", "end_user not found")
    if str(end_user.workspace_id) != str(workspace_id):
        return fail(
            BizCode.PERMISSION_DENIED.value,
            "该终端用户不属于当前工作空间",
            "end_user workspace mismatch",
        )

    # A merged origin id resolves to its target above; the info row of the
    # target is the one that must be returned.
    info_record = EndUserInfoRepository(db).get_end_user_info(end_user.id)
    if not info_record:
        return fail(
            BizCode.USER_NOT_FOUND.value,
            "终端用户信息记录不存在",
            "终端用户信息记录不存在",
        )

    top_fields = ("other_name", "aliases")
    meta_fields = (
        "relations",
        "goals",
        "core_facts",
        "interests",
        "traits",
        "beliefs_or_stances",
        "anchors",
        "events",
    )
    always_include = {"other_name"}
    max_visible = 6

    raw_meta = info_record.meta_data or {}
    candidates = [(field, getattr(info_record, field, None), True) for field in top_fields] + [
        (field, raw_meta.get(field), False) for field in meta_fields
    ]

    selected_top: dict[str, Any] = {}
    filtered_meta: dict[str, Any] = {}
    for field, value, is_top in candidates:
        if len(selected_top) + len(filtered_meta) >= max_visible:
            break
        if not value and field not in always_include:
            continue
        (selected_top if is_top else filtered_meta)[field] = value

    response_data = {
        "end_user_info_id": str(info_record.id),
        "end_user_id": str(info_record.end_user_id),
        **selected_top,
        "meta_data": filtered_meta,
        "created_at": to_timestamp_ms(info_record.created_at),
        "updated_at": to_timestamp_ms(info_record.updated_at),
    }
    return success(data=response_data, msg="查询成功")


@router.post("/info/update", summary="更新终端用户信息", response_model=ApiResponse)
def update_end_user_info(
    payload: end_user_info_schema.EndUserInfoUpdate,
    db: DbSession,
    principal: Caller,
    upstream: Upstream,
):
    workspace_id = principal.workspace_id
    end_user_id = payload.end_user_id

    if workspace_id is None:
        logger.warning("End-user info update without a workspace: actor=%s", principal.actor_id)
        return fail(
            BizCode.INVALID_PARAMETER.value,
            "请先切换到一个工作空间",
            "current_workspace_id is None",
        )

    # The id goes to the query untouched (host parity: a malformed string
    # fails at the database layer).
    end_user = EndUserRepository(db).get_end_user_by_id(end_user_id)
    if end_user is None:
        return fail(BizCode.USER_NOT_FOUND.value, "终端用户不存在", "end_user not found")
    if str(end_user.workspace_id) != str(workspace_id):
        logger.warning(
            "Actor %s tried to update end user %s outside workspace %s",
            principal.actor_id,
            end_user_id,
            workspace_id,
        )
        return fail(
            BizCode.PERMISSION_DENIED.value,
            "该终端用户不属于当前工作空间",
            "end_user workspace mismatch",
        )

    # A merged origin id resolves to its target above; writes must land on the
    # target (the origin row is soft-deleted).
    update_data = payload.model_dump(exclude_unset=True, exclude={"end_user_id"})
    outcome = EndUserService(db).update_end_user_info(str(end_user.id), update_data)

    if outcome.forward:
        try:
            return _forward_response(
                upstream,
                "/v1/end_user/info/update",
                payload.model_dump(exclude_unset=True),
            )
        except UpstreamUnavailableError as error:
            logger.error(
                "Alias-sync forward failed: end_user_id=%s, error=%s",
                end_user_id,
                error,
                exc_info=True,
            )
            return fail(BizCode.INTERNAL_ERROR.value, "终端用户信息更新失败", str(error))

    if outcome.error is not None:
        if outcome.error == "终端用户信息记录不存在":
            return fail(BizCode.USER_NOT_FOUND.value, "终端用户信息记录不存在", outcome.error)
        if outcome.error == "无效的终端用户ID格式":
            return fail(BizCode.INVALID_USER_ID.value, "无效的终端用户ID格式", outcome.error)
        logger.error(
            "End-user info update failed: end_user_id=%s, error=%s", end_user_id, outcome.error
        )
        return fail(BizCode.INTERNAL_ERROR.value, "终端用户信息更新失败", outcome.error)

    return success(data=outcome.data, msg="更新成功")


@router.post(
    "/identity/update",
    summary="修改终端用户身份标识（确认 / 归并）",
    response_model=ApiResponse,
)
def update_end_user_identity(
    payload: end_user_schema.EndUserIdentityUpdate,
    db: DbSession,
    principal: Caller,
    upstream: Upstream,
):
    workspace_id = principal.workspace_id
    source_end_user_id = payload.end_user_id

    if workspace_id is None:
        logger.warning("End-user identity update without a workspace: actor=%s", principal.actor_id)
        return fail(
            BizCode.INVALID_PARAMETER.value,
            "请先切换到一个工作空间",
            "current_workspace_id is None",
        )

    try:
        end_user_uuid = UUID(source_end_user_id)
    except (ValueError, AttributeError, TypeError):
        return fail(BizCode.INVALID_USER_ID.value, "无效的终端用户ID格式", "invalid uuid")

    try:
        end_user = EndUserRepository(db).get_end_user_by_id(end_user_uuid)
        if end_user is None:
            return fail(BizCode.USER_NOT_FOUND.value, "终端用户不存在", "end_user not found")
        if str(end_user.workspace_id) != str(workspace_id):
            logger.warning(
                "Actor %s tried to update identity of end user %s outside workspace %s",
                principal.actor_id,
                source_end_user_id,
                workspace_id,
            )
            return fail(
                BizCode.PERMISSION_DENIED.value,
                "该终端用户不属于当前工作空间",
                "end_user workspace mismatch",
            )

        outcome = EndUserService(db).confirm_identity(
            workspace_id=workspace_id,
            current_end_user=end_user,
            identity_features=payload.identity_features,
        )
        if outcome.forward:
            # Merge hit: the cross-channel merge (PG reference migration, Neo4j
            # reassignment, reflection dispatch) runs on the monolith; this
            # transaction was rolled back and holds no lock.
            return _forward_response(
                upstream, "/v1/end_user/identity/update", payload.model_dump()
            )

        raw_features = payload.identity_features
        clean_features = raw_features.strip() if raw_features and raw_features.strip() else None
        response_data = {
            "end_user_id": str(outcome.final_id),
            "source_end_user_id": source_end_user_id,
            "identity_features": clean_features,
            "identity_status": outcome.identity_status,
            "merged": outcome.merged,
        }
        logger.info(
            "End-user identity updated: source=%s, final=%s, status=%s, merged=%s",
            source_end_user_id,
            outcome.final_id,
            outcome.identity_status,
            outcome.merged,
        )
        return success(data=response_data, msg="身份标识更新成功")
    except Exception as error:
        logger.error(
            "End-user identity update failed: end_user_id=%s, error=%s",
            source_end_user_id,
            error,
            exc_info=True,
        )
        return fail(BizCode.INTERNAL_ERROR.value, "身份标识更新失败", str(error))


@router.post("/create", summary="创建 / 获取终端用户", response_model=ApiResponse)
def create_end_user(
    body: Annotated[dict[str, Any], Body()],
    _quota: EndUserQuota,
    db: DbSession,
    principal: Caller,
):
    """Create (or fetch) the workspace end user for ``other_id`` and connect it
    to a memory config (explicit id > workspace default)."""

    payload = end_user_schema.CreateEndUserRequest(**body)
    workspace_id = principal.workspace_id
    if workspace_id is None:
        # The quota dependency already rejects a missing workspace; this keeps
        # the type honest.
        raise InternalServerError()

    logger.info(
        "Create end user request - other_id: %s, workspace_id: %s",
        payload.other_id,
        workspace_id,
    )

    memory_config_id: UUID | None = None
    if payload.memory_config_id:
        try:
            requested_config_id = UUID(payload.memory_config_id)
        except ValueError as error:
            raise BusinessException(
                f"Invalid memory_config_id format: {payload.memory_config_id}",
                BizCode.INVALID_PARAMETER,
            ) from error
        config = cross_domain_read.get_memory_config_with_fallback(
            db, requested_config_id, workspace_id
        )
        if config is None:
            raise BusinessException(
                f"Memory config not found: {payload.memory_config_id}",
                BizCode.MEMORY_CONFIG_NOT_FOUND,
            )
        memory_config_id = config.config_id
    else:
        default_config = cross_domain_read.get_workspace_default_memory_config(db, workspace_id)
        if default_config is not None:
            memory_config_id = default_config.config_id
            logger.info("Using workspace default memory config: %s", memory_config_id)
        else:
            logger.warning("No default memory config found for workspace: %s", workspace_id)

    app_id: UUID | None = None
    if payload.app_id:
        try:
            app_id = UUID(payload.app_id)
        except ValueError as error:
            raise BusinessException(
                f"Invalid app_id format: {payload.app_id}",
                BizCode.INVALID_PARAMETER,
            ) from error

    end_user = EndUserRepository(db).get_or_create_with_config(
        app_id=app_id,
        workspace_id=workspace_id,
        other_id=payload.other_id,
        memory_config_id=memory_config_id,
        other_name=payload.other_name,
    )
    end_user.other_name = payload.other_name
    db.commit()
    logger.info("End user ready: %s", end_user.id)

    # The end user is committed; evaluate the usage alert from the
    # authoritative count.
    report_quota_change_sync(principal.tenant_id, "end_user_quota", workspace_id=workspace_id)

    result = {
        "id": str(end_user.id),
        "other_id": end_user.other_id or "",
        "other_name": end_user.other_name or "",
        "workspace_id": str(end_user.workspace_id),
        "memory_config_id": str(end_user.memory_config_id) if end_user.memory_config_id else None,
    }
    return success(
        data=end_user_schema.CreateEndUserResponse(**result).model_dump(),
        msg="End user created successfully",
    )


@router.get("/mapping", summary="查询终端用户映射", response_model=ApiResponse)
def get_end_user_mapping(
    db: DbSession,
    principal: Caller,
    end_user_id: str | None = Query(None, description="Filter by end_user_id (UUID)"),
    other_id: str | None = Query(None, description="Filter by other_id (exact match)"),
    other_name: str | None = Query(None, description="Filter by other_name (fuzzy match)"),
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数，最大 100"),
):
    workspace_id = principal.workspace_id
    if workspace_id is None:
        raise InternalServerError()

    logger.info(
        "Query end user mapping for workspace: %s, "
        "filters: end_user_id=%s, other_id=%s, other_name=%s",
        workspace_id,
        end_user_id,
        other_id,
        other_name,
    )

    end_user_id_uuid: UUID | None = None
    if end_user_id:
        try:
            end_user_id_uuid = UUID(end_user_id)
        except ValueError as error:
            raise BusinessException(
                f"Invalid end_user_id format: {end_user_id}",
                BizCode.INVALID_PARAMETER,
            ) from error

    end_users, total = EndUserRepository(db).get_filtered_by_workspace(
        workspace_id=workspace_id,
        end_user_id=end_user_id_uuid,
        other_id=other_id,
        other_name=other_name,
        limit=page_size,
        offset=(page - 1) * page_size,
    )
    user_items = [
        end_user_schema.EndUserMappingItem(
            end_user_id=str(user.id),
            other_id=user.other_id or "",
            other_name=user.other_name or "",
        )
        for user in end_users
    ]

    return success(
        data=end_user_schema.EndUserMappingResponse(
            items=user_items,
            page=PageMeta(
                page=page,
                pagesize=page_size,
                total=total,
                hasnext=(page * page_size) < total,
            ),
        ).model_dump(),
        msg="End user mapping retrieved successfully",
    )


__all__ = ["router"]
