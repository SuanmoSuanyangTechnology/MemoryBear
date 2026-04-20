"""API Key Service"""
import time
import uuid
import math
from typing import Optional, Tuple
from datetime import datetime, timedelta

from sqlalchemy.orm import Session
from sqlalchemy import select

from app.aioRedis import aio_redis
from app.models.api_key_model import ApiKey
from app.repositories.api_key_repository import ApiKeyRepository, ApiKeyLogRepository
from app.schemas import api_key_schema
from app.schemas.response_schema import PageData, PageMeta
from app.core.api_key_utils import generate_api_key
from app.core.exceptions import (
    BusinessException,
)
from app.core.error_codes import BizCode
from app.core.logging_config import get_business_logger
from app.i18n.exceptions import I18nException

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

            # 校验 rate_limit 不能超过租户套餐的 api_ops_rate_limit
            from app.models.workspace_model import Workspace
            from app.core.quota_manager import get_api_ops_rate_limit

            workspace = db.query(Workspace).filter(Workspace.id == workspace_id).first()
            if workspace:
                tenant_api_ops_limit = get_api_ops_rate_limit(db, workspace.tenant_id)
                if tenant_api_ops_limit and data.rate_limit > tenant_api_ops_limit:
                    raise I18nException(
                        error_key="errors.api.api_key_rate_limit_exceeded",
                        status_code=400,
                        error_code="API_KEY_RATE_LIMIT_EXCEEDED",
                        rate_limit=data.rate_limit,
                        limit=tenant_api_ops_limit,
                    )

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

        # 校验 rate_limit 不能超过租户套餐的 api_ops_rate_limit
        if data.rate_limit is not None:
            from app.models.workspace_model import Workspace
            from app.core.quota_manager import get_api_ops_rate_limit

            workspace = db.query(Workspace).filter(Workspace.id == workspace_id).first()
            if workspace:
                tenant_api_ops_limit = get_api_ops_rate_limit(db, workspace.tenant_id)
                if tenant_api_ops_limit and data.rate_limit > tenant_api_ops_limit:
                    raise I18nException(
                        error_key="errors.api.api_key_rate_limit_exceeded",
                        status_code=400,
                        error_code="API_KEY_RATE_LIMIT_EXCEEDED",
                        rate_limit=data.rate_limit,
                        limit=tenant_api_ops_limit,
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

    async def check_tenant_rate_limit(self, window_id: uuid.UUID, limit: int) -> Tuple[bool, dict]:
        """
        按 window_id（api_key_id）做 1 秒滑动窗口限速。
        限制值来自套餐配额 api_ops_rate_limit，每个 API Key 独立受此上限约束。
        只有请求被允许时才计入窗口，超限请求不污染计数。
        """
        now = time.time()
        window_start = now - 1  # 1 秒窗口
        key = f"rate_limit:tenant_qps:{window_id}"

        async with self.redis.pipeline() as pipe:
            pipe.zremrangebyscore(key, 0, window_start)
            pipe.zcard(key)
            results = await pipe.execute()

        current = results[1]

        if current >= limit:
            return False, {
                "limit": limit,
                "remaining": 0,
                "reset": int(now) + 1,
            }

        member = f"{now}:{uuid.uuid4().hex}"
        async with self.redis.pipeline() as pipe:
            pipe.zadd(key, {member: now})
            pipe.expire(key, 2)
            await pipe.execute()

        return True, {
            "limit": limit,
            "remaining": max(0, limit - current - 1),
            "reset": int(now) + 1,
        }

    async def check_qps(self, api_key_id: uuid.UUID, limit: int) -> Tuple[bool, dict]:
        """
        检查 API Key 自身 QPS 限制（1 秒滑动窗口）。
        只有请求被允许时才计入窗口，超限请求不污染计数。
        """
        now = time.time()
        window_start = now - 1
        key = f"rate_limit:qps:{api_key_id}"

        async with self.redis.pipeline() as pipe:
            pipe.zremrangebyscore(key, 0, window_start)
            pipe.zcard(key)
            results = await pipe.execute()

        current = results[1]

        if current >= limit:
            return False, {
                "limit": limit,
                "remaining": 0,
                "reset": int(now) + 1,
            }

        member = f"{now}:{uuid.uuid4().hex}"
        async with self.redis.pipeline() as pipe:
            pipe.zadd(key, {member: now})
            pipe.expire(key, 2)
            await pipe.execute()

        return True, {
            "limit": limit,
            "remaining": max(0, limit - current - 1),
            "reset": int(now) + 1,
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
        1. API Key 自身 QPS（每个 key 独立，不超过其配置的 rate_limit）
        2. 租户套餐 api_ops_rate_limit（每个 key 独立受套餐限额约束）
        3. API Key 日调用量

        设计语义：
        - 租户下 N 个 API Key 可以并发调用，互不影响
        - 每个 API Key 独立受自身 rate_limit 约束
        - 每个 API Key 独立受租户套餐 api_ops_rate_limit 约束（per-key 限额）
        """
        # 1. 检查 API Key 自身 QPS
        qps_ok, qps_info = await self.check_qps(
            api_key.id,
            api_key.rate_limit
        )
        if not qps_ok:
            return False, "QPS limit exceeded", {
                "X-RateLimit-Limit-QPS": str(qps_info["limit"]),
                "X-RateLimit-Remaining-QPS": str(qps_info["remaining"]),
                "X-RateLimit-Reset": str(qps_info["reset"])
            }

        # 2. 检查租户套餐 api_ops_rate_limit（运行时流量控制）
        # 每个 API Key 的速率都不能超过租户套餐上限，无论 key 自身配置多少
        tenant_info = None
        if db is not None:
            try:
                from app.models.workspace_model import Workspace
                from app.core.quota_manager import get_api_ops_rate_limit

                # 从 Redis 缓存取 tenant_limit，避免每次请求都查数据库
                # TTL 60 秒，套餐变更后最多 1 分钟生效
                # 用 workspace_id 作为滑动窗口维度（一个 workspace 只属于一个 tenant，语义等价）
                cache_key = f"tenant_api_ops_limit:{api_key.workspace_id}"
                cached = await self.redis.get(cache_key)
                if cached is not None:
                    try:
                        tenant_limit = int(cached) if cached != "0" else None
                    except (ValueError, TypeError):
                        # 缓存数据损坏，回源查数据库
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
                    # 用 api_key.id 作为滑动窗口 key，每个 API Key 独立受 api_ops_rate_limit 约束
                    tenant_ok, tenant_info = await self.check_tenant_rate_limit(
                        api_key.id, tenant_limit
                    )
                    if not tenant_ok:
                        return False, "API ops rate limit exceeded", {
                            "X-RateLimit-Limit-QPS": str(tenant_limit),
                            "X-RateLimit-Remaining-QPS": str(tenant_info["remaining"]),
                            "X-RateLimit-Reset": str(tenant_info["reset"]),
                        }
            except Exception as e:
                logger.warning(f"租户 api_ops_rate_limit 运行时检查失败，跳过: {e}")

        # 3. 检查日调用量
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

        headers = {
            "X-RateLimit-Limit-QPS": str(qps_info["limit"]),
            "X-RateLimit-Remaining-QPS": str(qps_info["remaining"]),
            "X-RateLimit-Limit-Day": str(daily_info["limit"]),
            "X-RateLimit-Remaining-Day": str(daily_info["remaining"]),
            "X-RateLimit-Reset": str(daily_info["reset"]),
        }
        # 如果租户限速信息存在，补充到响应头
        if tenant_info:
            headers["X-RateLimit-Limit-Tenant-QPS"] = str(tenant_info["limit"])
            headers["X-RateLimit-Remaining-Tenant-QPS"] = str(tenant_info["remaining"])
        return True, "", headers


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
