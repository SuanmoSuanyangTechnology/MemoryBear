"""API-key management service (sync, host-compatible).

Port of ``core/api/app/services/api_key_service.py`` minus the rate limiter
and API-key authentication helpers (not on the management plane). Snapshot
invalidation follows decision D11: creations and snapshot-field updates carry
the plaintext key; deletions and regenerations first revoke the old hash.
"""

from __future__ import annotations

import logging
import math
import secrets
import uuid

from error_core.codes import BizCode
from sqlalchemy.orm import Session

from src.core.exceptions import BusinessException
from src.invalidation_notify import (
    api_key_hash,
    notify_api_key_created_sync,
    notify_api_key_sync,
)
from src.models.api_key_model import ApiKey, ApiKeyType
from src.models.app_mirror_model import AppType
from src.repositories import api_key_repository, management
from src.schemas import api_key_schema
from src.schemas.response_schema import PageData, PageMeta
from src.services import quota

logger = logging.getLogger(__name__)

# Fields the gateway snapshot is built from; only these changes need a rebuild.
_SNAPSHOT_FIELDS = frozenset(
    {"scopes", "rate_limit", "daily_request_limit", "is_active", "expires_at"}
)

_KEY_PREFIXES = {
    ApiKeyType.AGENT: "sk-agent-",
    ApiKeyType.CLUSTER: "sk-multi_agent-",
    ApiKeyType.WORKFLOW: "sk-workflow-",
    ApiKeyType.PURE_WORKFLOW: "sk-workflow-",
    ApiKeyType.SERVICE: "sk-service-",
}

_APP_TYPE_BY_KEY_TYPE = {
    ApiKeyType.AGENT: AppType.AGENT.value,
    ApiKeyType.CLUSTER: AppType.MULTI_AGENT.value,
    ApiKeyType.WORKFLOW: AppType.WORKFLOW.value,
    ApiKeyType.PURE_WORKFLOW: AppType.PURE_WORKFLOW.value,
}


def _generate_api_key(key_type: ApiKeyType) -> str:
    return f"{_KEY_PREFIXES[key_type]}{secrets.token_urlsafe(32)[:32]}"


def create_api_key(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    data: api_key_schema.ApiKeyCreate,
) -> ApiKey:
    """创建 API Key（host 同义；明文只经 Redis 通知，不进日志）"""

    try:
        if api_key_repository.get_active_by_name(
            db,
            workspace_id=workspace_id,
            resource_id=data.resource_id,
            name=data.name,
        ):
            raise BusinessException(
                f"API Key 名称 {data.name} 已存在",
                code=BizCode.API_KEY_DUPLICATE_NAME,
            )

        # Tenant QPS ceiling only applies when the workspace row exists (host).
        workspace = management.get_workspace_by_id(db, workspace_id)
        if workspace:
            tenant_api_ops_limit = quota.get_api_ops_rate_limit(db, workspace.tenant_id)
            if tenant_api_ops_limit and data.rate_limit > tenant_api_ops_limit:
                raise BusinessException(
                    f"API Key QPS 不能超过套餐上限 {tenant_api_ops_limit}",
                    code=BizCode.BAD_REQUEST,
                )

        if data.type == ApiKeyType.SERVICE:
            if "app" in data.scopes:
                raise BusinessException(
                    "SERVICE 类型 API Key 的权限范围不能包含 app",
                    code=BizCode.BAD_REQUEST,
                )
            if not data.resource_id:
                raise BusinessException(
                    "SERVICE 类型 API Key 必须指定 resource_id（指向 workspace）",
                    code=BizCode.BAD_REQUEST,
                )
        else:
            if "app" not in data.scopes:
                raise BusinessException(
                    f"{data.type} 类型 API Key 的权限范围必须包含 app",
                    code=BizCode.BAD_REQUEST,
                )
            if not data.resource_id:
                raise BusinessException(
                    f"{data.type} 类型 API Key 必须指定 resource_id（指向应用）",
                    code=BizCode.BAD_REQUEST,
                )

            expected_app_type = _APP_TYPE_BY_KEY_TYPE.get(data.type)
            app = api_key_repository.get_app_by_id(db, data.resource_id)
            if not app:
                raise BusinessException("关联的应用不存在", code=BizCode.BAD_REQUEST)
            if expected_app_type and app.type != expected_app_type:
                raise BusinessException(
                    f"API Key 类型 {data.type} 与应用类型 {app.type} 不匹配，"
                    f"期望应用类型为 {expected_app_type}",
                    code=BizCode.BAD_REQUEST,
                )
            if not app.current_release_id:
                raise BusinessException("该应用未发布", code=BizCode.APP_NOT_PUBLISHED)

        api_key = _generate_api_key(data.type)
        api_key_obj = api_key_repository.create(
            db,
            {
                "id": uuid.uuid4(),
                "name": data.name,
                "description": data.description,
                "api_key": api_key,
                "type": data.type,
                "scopes": data.scopes,
                "workspace_id": workspace_id,
                "resource_id": data.resource_id,
                "rate_limit": data.rate_limit,
                "daily_request_limit": data.daily_request_limit,
                "quota_limit": data.quota_limit,
                "expires_at": data.expires_at,
                "created_by": user_id,
            },
        )
        db.commit()

        logger.info("API Key 创建成功")
        notify_api_key_created_sync(api_key)
        return api_key_obj
    except Exception as exc:
        db.rollback()
        logger.error("API Key 创建失败: %s", exc)
        raise


def get_api_key(
    db: Session, api_key_id: uuid.UUID, workspace_id: uuid.UUID
) -> ApiKey:
    api_key = api_key_repository.get_by_id(db, api_key_id)
    if not api_key:
        raise BusinessException(
            f"API Key {api_key_id} 不存在", code=BizCode.API_KEY_NOT_FOUND
        )
    if api_key.workspace_id != workspace_id:
        raise BusinessException("无权访问此 API Key", code=BizCode.FORBIDDEN)
    return api_key


def list_api_keys(
    db: Session, workspace_id: uuid.UUID, query: api_key_schema.ApiKeyQuery
) -> PageData:
    items, total = api_key_repository.list_by_workspace(db, workspace_id, query)
    pages = math.ceil(total / query.pagesize) if total > 0 else 0

    return PageData(
        page=PageMeta(
            page=query.page,
            pagesize=query.pagesize,
            total=total,
            hasnext=query.page < pages,
        ),
        items=[api_key_schema.ApiKey.model_validate(item) for item in items],
    )


def update_api_key(
    db: Session,
    api_key_id: uuid.UUID,
    workspace_id: uuid.UUID,
    data: api_key_schema.ApiKeyUpdate,
) -> ApiKey:
    api_key = get_api_key(db, api_key_id, workspace_id)

    if data.name and data.name != api_key.name:
        if api_key_repository.get_active_by_name(
            db,
            workspace_id=workspace_id,
            resource_id=api_key.resource_id,
            name=data.name,
            exclude_id=api_key_id,
        ):
            raise BusinessException(
                f"API Key 名称 {data.name} 已存在",
                code=BizCode.API_KEY_DUPLICATE_NAME,
            )

    if data.rate_limit is not None:
        workspace = management.get_workspace_by_id(db, workspace_id)
        if workspace:
            tenant_api_ops_limit = quota.get_api_ops_rate_limit(db, workspace.tenant_id)
            if tenant_api_ops_limit and data.rate_limit > tenant_api_ops_limit:
                raise BusinessException(
                    f"API Key QPS 不能超过套餐上限 {tenant_api_ops_limit}",
                    code=BizCode.BAD_REQUEST,
                )

    update_data = data.model_dump(exclude_unset=True)
    # The plaintext never changes on update; capture it before commit/refresh.
    plain = api_key.api_key
    api_key_repository.update(db, api_key_id, update_data)
    db.commit()
    db.refresh(api_key)

    logger.info("API Key 更新成功")
    # Snapshot fields changed -> rebuild via the plaintext notification.
    if _SNAPSHOT_FIELDS & update_data.keys():
        notify_api_key_created_sync(plain)
    return api_key


def delete_api_key(
    db: Session, api_key_id: uuid.UUID, workspace_id: uuid.UUID
) -> bool:
    api_key = get_api_key(db, api_key_id, workspace_id)
    # Capture the plaintext before commit (expire_on_commit triggers a reload).
    old_plain = api_key.api_key

    api_key_repository.delete(db, api_key_id)
    db.commit()

    logger.info("API Key 删除成功")
    notify_api_key_sync(api_key_hash(old_plain))
    return True


def regenerate_api_key(
    db: Session, api_key_id: uuid.UUID, workspace_id: uuid.UUID
) -> ApiKey:
    api_key = get_api_key(db, api_key_id, workspace_id)

    if not api_key.is_active:
        raise BusinessException(
            "无法重新生成已停用的 API Key", code=BizCode.API_KEY_INACTIVE
        )

    new_api_key = _generate_api_key(ApiKeyType(api_key.type))

    # Revoke the old hash first; the plaintext message rebuilds the new snapshot.
    notify_api_key_sync(api_key_hash(api_key.api_key))

    api_key_repository.update(db, api_key_id, {"api_key": new_api_key})
    db.commit()
    db.refresh(api_key)

    notify_api_key_created_sync(new_api_key)

    logger.info("API Key 重新生成成功")
    return api_key


def get_stats(
    db: Session, api_key_id: uuid.UUID, workspace_id: uuid.UUID
) -> api_key_schema.ApiKeyStats:
    get_api_key(db, api_key_id, workspace_id)
    stats_data = api_key_repository.get_stats(db, api_key_id)
    return api_key_schema.ApiKeyStats(**stats_data)


def get_logs(
    db: Session,
    api_key_id: uuid.UUID,
    workspace_id: uuid.UUID,
    filters: dict,
    page: int,
    pagesize: int,
) -> PageData:
    get_api_key(db, api_key_id, workspace_id)

    items, total = api_key_repository.list_logs_by_api_key(
        db, api_key_id, filters, page, pagesize
    )
    pages = math.ceil(total / pagesize) if total > 0 else 0

    return PageData(
        page=PageMeta(
            page=page,
            pagesize=pagesize,
            total=total,
            hasnext=page < pages,
        ),
        items=[api_key_schema.ApiKeyLog.model_validate(item) for item in items],
    )
