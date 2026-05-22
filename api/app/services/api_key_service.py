"""API Key Service"""
import time
import uuid
import math
from typing import Optional, Tuple
from datetime import datetime, timedelta

from sqlalchemy.orm import Session
from sqlalchemy import select

from app.aioRedis import aio_redis
from app.models.api_key_model import ApiKey, ApiKeyType
from app.repositories.api_key_repository import ApiKeyRepository, ApiKeyLogRepository
from app.schemas import api_key_schema
from app.schemas.response_schema import PageData, PageMeta
from app.core.api_key_utils import generate_api_key
from app.core.exceptions import (
    BusinessException,
)
from app.core.error_codes import BizCode
from app.core.logging_config import get_business_logger
from app.models.app_model import App

logger = get_business_logger()


class ApiKeyService:
    """API Key 业务逻辑服务"""

    @staticmethod
    def create_api_key(
            db: Session,
            *,
            workspace_id: uuid.UUID,
            user_id: uuid.UUID,
            data: api_key_schema.ApiKeyCreate
    ) -> ApiKey:
        """
        创建 API Key
        Returns:
            ApiKey: API Key 对象
        """
        try:
            existing = db.scalar(
                select(ApiKey).where(
                    ApiKey.workspace_id == workspace_id,
                    ApiKey.resource_id == data.resource_id,
                    ApiKey.name == data.name,
                    ApiKey.is_active
                )
            )
            if existing:
                raise BusinessException(f"API Key 名称 {data.name} 已存在", BizCode.API_KEY_DUPLICATE_NAME)

            # 若 rate_limit 超过租户套餐的 api_ops_rate_limit，直接报错
            from app.models.workspace_model import Workspace
            from app.core.quota_manager import get_api_ops_rate_limit

            workspace = db.query(Workspace).filter(Workspace.id == workspace_id).first()
            if workspace:
                tenant_api_ops_limit = get_api_ops_rate_limit(db, workspace.tenant_id)
                if tenant_api_ops_limit and data.rate_limit > tenant_api_ops_limit:
                    raise BusinessException(
                        f"API Key QPS 不能超过套餐上限 {tenant_api_ops_limit}",
                        BizCode.BAD_REQUEST
                    )

            if data.type == ApiKeyType.SERVICE.value:
                if "app" in data.scopes:
                    raise BusinessException("SERVICE 类型 API Key 的权限范围不能包含 app", BizCode.BAD_REQUEST)
                if not data.resource_id:
                    raise BusinessException("SERVICE 类型 API Key 必须指定 resource_id（指向 workspace）", BizCode.BAD_REQUEST)
            else:
                if "app" not in data.scopes:
                    raise BusinessException(f"{data.type} 类型 API Key 的权限范围必须包含 app", BizCode.BAD_REQUEST)
                if not data.resource_id:
                    raise BusinessException(f"{data.type} 类型 API Key 必须指定 resource_id（指向应用）", BizCode.BAD_REQUEST)

                from app.models.app_model import AppType

                type_app_type_map = {
                    ApiKeyType.AGENT.value: AppType.AGENT.value,
                    ApiKeyType.CLUSTER.value: AppType.MULTI_AGENT.value,
                    ApiKeyType.WORKFLOW.value: AppType.WORKFLOW.value,
                }
                expected_app_type = type_app_type_map.get(data.type)

                app = db.get(App, data.resource_id)
                if not app:
                    raise BusinessException("关联的应用不存在", BizCode.BAD_REQUEST)
                if expected_app_type and app.type != expected_app_type:
                    raise BusinessException(
                        f"API Key 类型 {data.type} 与应用类型 {app.type} 不匹配，期望应用类型为 {expected_app_type}",
                        BizCode.BAD_REQUEST
                    )
                if not app.current_release_id:
                    raise BusinessException("该应用未发布", BizCode.APP_NOT_PUBLISHED)

            # 生成 API Key
            api_key = generate_api_key(data.type)

            # 创建数据
            api_key_data = {
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
            }

            api_key_obj = ApiKeyRepository.create(db, api_key_data)
            db.commit()

            logger.info("API Key 创建成功", extra={
                "api_key_id": str(api_key_obj.id),
                "workspace_id": str(workspace_id),
                "api_key_name": data.name,
                "type": data.type
            })

            return api_key_obj

        except Exception as e:
            db.rollback()
            logger.error(f"API Key 创建失败: {e}", extra={
                "workspace_id": str(workspace_id),
                "api_key_name": getattr(data, 'name', 'unknown'),
                "error": str(e)
            })
            raise

    @staticmethod
    def get_api_key(
            db: Session,
            api_key_id: uuid.UUID,
            workspace_id: uuid.UUID
    ) -> ApiKey:
        """获取 API Key"""
        api_key = ApiKeyRepository.get_by_id(db, api_key_id)
        if not api_key:
            raise BusinessException(f"API Key {api_key_id} 不存在", BizCode.API_KEY_NOT_FOUND)

        if api_key.workspace_id != workspace_id:
            raise BusinessException("无权访问此 API Key", BizCode.FORBIDDEN)

        return api_key

    @staticmethod
    def list_api_keys(
            db: Session,
            workspace_id: uuid.UUID,
            query: api_key_schema.ApiKeyQuery
    ) -> PageData:
        """列出 API Keys"""
        items, total = ApiKeyRepository.list_by_workspace(db, workspace_id, query)
        pages = math.ceil(total / query.pagesize) if total > 0 else 0

        return PageData(
            page=PageMeta(
                page=query.page,
                pagesize=query.pagesize,
                total=total,
                hasnext=query.page < pages
            ),
            items=[api_key_schema.ApiKey.model_validate(item) for item in items]
        )

    @staticmethod
    def update_api_key(
            db: Session,
            api_key_id: uuid.UUID,
            workspace_id: uuid.UUID,
            data: api_key_schema.ApiKeyUpdate
    ) -> ApiKey:
        """更新 API Key配置"""
        api_key = ApiKeyService.get_api_key(db, api_key_id, workspace_id)

        # 检查名称重复
        if data.name and data.name != api_key.name:
            existing = db.scalar(
                select(ApiKey).where(
                    ApiKey.workspace_id == workspace_id,
                    ApiKey.resource_id == api_key.resource_id,
                    ApiKey.name == data.name,
                    ApiKey.is_active,
                    ApiKey.id != api_key_id
                )
            )
            if existing:
                raise BusinessException(f"API Key 名称 {data.name} 已存在", BizCode.API_KEY_DUPLICATE_NAME)

        # 若 rate_limit 超过租户套餐的 api_ops_rate_limit，直接报错
        if data.rate_limit is not None:
            from app.models.workspace_model import Workspace
            from app.core.quota_manager import get_api_ops_rate_limit

            workspace = db.query(Workspace).filter(Workspace.id == workspace_id).first()
            if workspace:
                tenant_api_ops_limit = get_api_ops_rate_limit(db, workspace.tenant_id)
                if tenant_api_ops_limit and data.rate_limit > tenant_api_ops_limit:
                    raise BusinessException(
                        f"API Key QPS 不能超过套餐上限 {tenant_api_ops_limit}",
                        BizCode.BAD_REQUEST
                    )

        update_data = data.model_dump(exclude_unset=True)
        ApiKeyRepository.update(db, api_key_id, update_data)
        db.commit()
        db.refresh(api_key)

        logger.info("API Key 更新成功", extra={"api_key_id": str(api_key_id)})
        return api_key

    @staticmethod
    def delete_api_key(
            db: Session,
            api_key_id: uuid.UUID,
            workspace_id: uuid.UUID
    ) -> bool:
        """删除 API Key"""
        api_key = ApiKeyService.get_api_key(db, api_key_id, workspace_id)

        ApiKeyRepository.delete(db, api_key_id)
        db.commit()

        logger.info("API Key 删除成功", extra={"api_key_id": str(api_key_id)})
        return True

    @staticmethod
    def regenerate_api_key(
            db: Session,
            api_key_id: uuid.UUID,
            workspace_id: uuid.UUID
    ) -> ApiKey:
        """重新生成 API Key"""
        api_key = ApiKeyService.get_api_key(db, api_key_id, workspace_id)

        # 检查 API Key 是否激活
        if not api_key.is_active:
            raise BusinessException("无法重新生成已停用的 API Key", BizCode.API_KEY_INACTIVE)

        # 生成新的 API Key
        new_api_key = generate_api_key(api_key.type)

        # 更新
        ApiKeyRepository.update(db, api_key_id, {
            "api_key": new_api_key
        })
        db.commit()
        db.refresh(api_key)

        logger.info("API Key 重新生成成功", extra={"api_key_id": str(api_key_id)})
        return api_key

    @staticmethod
    def get_stats(
            db: Session,
            api_key_id: uuid.UUID,
            workspace_id: uuid.UUID
    ) -> api_key_schema.ApiKeyStats:
        """获取使用统计"""
        api_key = ApiKeyService.get_api_key(db, api_key_id, workspace_id)

        stats_data = ApiKeyRepository.get_stats(db, api_key_id)
        return api_key_schema.ApiKeyStats(**stats_data)

    @staticmethod
    def get_logs(
            db: Session,
            api_key_id: uuid.UUID,
            workspace_id: uuid.UUID,
            filters: dict,
            page: int,
            pagesize: int
    ) -> PageData:
        """获取 API Key 使用日志"""
        # 验证 API Key 权限
        api_key = ApiKeyService.get_api_key(db, api_key_id, workspace_id)

        items, total = ApiKeyLogRepository.list_by_api_key(
            db, api_key_id, filters, page, pagesize
        )

        # 计算分页信息
        pages = math.ceil(total / pagesize) if total > 0 else 0

        return PageData(
            page=PageMeta(
                page=page,
                pagesize=pagesize,
                total=total,
                hasnext=page < pages
            ),
            items=[api_key_schema.ApiKeyLog.model_validate(item) for item in items]
        )


class RateLimiterService:
    def __init__(self):
        self.redis = aio_redis

    async def check_qps(self, api_key_id: uuid.UUID, limit: int) -> Tuple[bool, dict]:
        """检查QPS限制

        Returns:
            (is_allowed, rate_limit_info)
        """
        key = f"rate_limit:qps:{api_key_id}"

        async with self.redis.pipeline() as pipe:
            pipe.incr(key)
            pipe.expire(key, 1, nx=True)  # 1 秒过期
            results = await pipe.execute()

        current = results[0]
        remaining = max(0, limit - current)
        reset_time = int(time.time()) + 1

        return current <= limit, {
            "limit": limit,
            "current": current,
            "remaining": remaining,
            "reset": reset_time,
        }

    async def check_daily_requests(
            self,
            api_key_id: uuid.UUID,
            limit: int
    ) -> Tuple[bool, dict]:
        """检查日调用量限制。
        使用原子 INCR，先写后判断，极低概率下允许轻微超限（并发场景下可接受）。
        """
        today = datetime.now().strftime("%Y%m%d")
        key = f"rate_limit:daily:{api_key_id}:{today}"

        now = datetime.now()
        tomorrow_0 = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        expire_seconds = int((tomorrow_0 - now).total_seconds())
        reset_time = int(tomorrow_0.timestamp())

        async with self.redis.pipeline() as pipe:
            pipe.incr(key)
            pipe.expire(key, expire_seconds, nx=True)
            results = await pipe.execute()

        current = results[0]

        if current > limit:
            return False, {
                "limit": limit,
                "remaining": 0,
                "reset": reset_time,
            }

        return True, {
            "limit": limit,
            "remaining": max(0, limit - current),
            "reset": reset_time,
        }

    async def check_all_limits(
            self,
            api_key: ApiKey,
            db: Optional[Session] = None,
    ) -> Tuple[bool, str, dict]:
        """
        检查所有限制，按以下顺序：
        1. API Key QPS：取 api_key.rate_limit 与套餐 api_ops_rate_limit 的最小值作为限额
        2. API Key 日调用量
        """
        # 1. 取套餐限额与 api_key 自身限额的最小值
        effective_limit = api_key.rate_limit
        if db is not None:
            try:
                from app.models.workspace_model import Workspace
                from app.core.quota_manager import get_api_ops_rate_limit

                cache_key = f"tenant_api_ops_limit:{api_key.workspace_id}"
                cached = await self.redis.get(cache_key)
                if cached is not None:
                    try:
                        tenant_limit = int(cached) if cached != "0" else None
                    except (ValueError, TypeError):
                        cached = None
                        tenant_limit = None

                if cached is None:
                    workspace = db.query(Workspace).filter(Workspace.id == api_key.workspace_id).first()
                    if workspace:
                        tenant_limit = get_api_ops_rate_limit(db, workspace.tenant_id)
                        await self.redis.set(cache_key, str(tenant_limit) if tenant_limit else "0", ex=60)
                    else:
                        tenant_limit = None

                if tenant_limit:
                    effective_limit = min(api_key.rate_limit, tenant_limit)
            except Exception as e:
                logger.warning(f"获取套餐限额失败，使用 api_key 自身限额: {e}")

        # 用最终有效限额做 QPS 检查
        qps_ok, qps_info = await self.check_qps(api_key.id, effective_limit)
        if not qps_ok:
            # 判断是套餐限额触发还是 api_key 自身限额触发
            if tenant_limit and effective_limit == tenant_limit and api_key.rate_limit > tenant_limit:
                error_msg = "Tenant limit exceeded"
            else:
                error_msg = "QPS limit exceeded"
            return False, error_msg, {
                "X-RateLimit-Limit-QPS": str(qps_info["limit"]),
                "X-RateLimit-Remaining-QPS": str(qps_info["remaining"]),
                "X-RateLimit-Reset": str(qps_info["reset"])
            }

        # 2. 检查日调用量
        daily_ok, daily_info = await self.check_daily_requests(
            api_key.id,
            api_key.daily_request_limit
        )
        if not daily_ok:
            return False, "Daily request limit exceeded", {
                "X-RateLimit-Limit-Day": str(daily_info["limit"]),
                "X-RateLimit-Remaining-Day": str(daily_info["remaining"]),
                "X-RateLimit-Reset": str(daily_info["reset"])
            }

        return True, "", {
            "X-RateLimit-Limit-QPS": str(qps_info["limit"]),
            "X-RateLimit-Remaining-QPS": str(qps_info["remaining"]),
            "X-RateLimit-Limit-Day": str(daily_info["limit"]),
            "X-RateLimit-Remaining-Day": str(daily_info["remaining"]),
            "X-RateLimit-Reset": str(daily_info["reset"]),
        }


class ApiKeyAuthService:
    @staticmethod
    def validate_api_key(
            db: Session,
            api_key: str
    ) -> Optional[ApiKey]:
        """
        验证API Key 有效性

        检查：
        1. API Key 是否存在
        2. is_active 是否为true
        3. expires_at 是否未过期
        4. quota 是否未超限
        """
        api_key_obj = ApiKeyRepository.get_by_api_key(db, api_key)

        if not api_key_obj:
            return None

        if not api_key_obj.is_active:
            return None

        if api_key_obj.expires_at and datetime.now() > api_key_obj.expires_at:
            return None

        if api_key_obj.quota_limit and api_key_obj.quota_used >= api_key_obj.quota_limit:
            return None

        return api_key_obj

    @staticmethod
    def check_app_published(db: Session, api_key_obj: ApiKey) -> None:
        """
        检查应用是否已发布，未发布则抛出异常
        SERVICE 类型的 api_key 不绑定应用（resource_id 指向 workspace），跳过校验
        """
        if not api_key_obj.resource_id:
            return
        if api_key_obj.type == ApiKeyType.SERVICE.value:
            return
        app = db.get(App, api_key_obj.resource_id)
        if not app or not app.current_release_id:
            raise BusinessException("应用未发布，不可用", BizCode.APP_NOT_PUBLISHED)

    @staticmethod
    def check_scope(api_key: ApiKey, required_scope: str) -> bool:
        """检查权限范围"""
        return required_scope in api_key.scopes

    @staticmethod
    def check_resource(
            api_key: ApiKey,
            resource_id: uuid.UUID
    ) -> bool:
        """检查资源绑定"""
        return api_key.resource_id == resource_id
