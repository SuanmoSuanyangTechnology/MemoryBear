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
from app.core.api_key_utils import generate_api_key, hash_api_key, validate_resource_binding
from app.core.exceptions import (
    BusinessException,
)
from app.core.error_codes import BizCode
from app.core.logging_config import get_business_logger

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
    ) -> Tuple[ApiKey, str]:
        """
        创建 API Key
        Returns:
            Tuple[ApiKey, str]: (API Key 对象, API Key 明文)
        """
        try:
            # 验证资源绑定
            if data.resource_type or data.resource_id:
                is_valid, error_msg = validate_resource_binding(
                    data.resource_type, str(data.resource_id) if data.resource_id else None
                )
                if not is_valid:
                    raise BusinessException(error_msg, BizCode.API_KEY_INVALID_RESOURCE)

            existing = db.scalar(
                select(ApiKey).where(
                    ApiKey.workspace_id == workspace_id,
                    ApiKey.name == data.name,
                    ApiKey.is_active
                )
            )
            if existing:
                raise BusinessException(f"API Key 名称 '{data.name}' 已存在", BizCode.API_KEY_DUPLICATE_NAME)

            # 生成 API Key
            api_key, key_hash, key_prefix = generate_api_key(data.type)

            # 创建数据
            api_key_data = {
                "id": uuid.uuid4(),
                "name": data.name,
                "description": data.description,
                "key_prefix": key_prefix,
                "key_hash": key_hash,
                "type": data.type,
                "scopes": data.scopes,
                "workspace_id": workspace_id,
                "resource_id": data.resource_id,
                "resource_type": data.resource_type,
                "rate_limit": data.rate_limit or 10,
                "daily_request_limit": data.daily_request_limit or 10000,
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

            return api_key_obj, api_key

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
                    ApiKey.name == data.name,
                    ApiKey.is_active,
                    ApiKey.id != api_key_id
                )
            )
            if existing:
                raise BusinessException(f"API Key 名称 '{data.name}' 已存在", BizCode.API_KEY_DUPLICATE_NAME)

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
    ) -> Tuple[ApiKey, str]:
        """重新生成 API Key"""
        api_key = ApiKeyService.get_api_key(db, api_key_id, workspace_id)

        # 检查 API Key 是否激活
        if not api_key.is_active:
            raise BusinessException("无法重新生成已停用的 API Key", BizCode.API_KEY_INACTIVE)

        # 生成新的 API Key
        new_api_key, key_hash, key_prefix = generate_api_key(api_key_schema.ApiKeyType(api_key.type))

        # 更新
        ApiKeyRepository.update(db, api_key_id, {
            "key_hash": key_hash,
            "key_prefix": key_prefix
        })
        db.commit()
        db.refresh(api_key)

        logger.info("API Key 重新生成成功", extra={"api_key_id": str(api_key_id)})
        return api_key, new_api_key

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
        """
        检查QPS限制
        Returns:
            (is_allowed, rate_limit_info)
        """
        key = f"rate_limit:qps:{api_key_id}"
        async with self.redis.pipeline() as pipe:
            pipe.incr(key)
            pipe.expire(key, 1)  # 1 秒过期
            results = await pipe.execute()

        current = results[0]
        remaining = max(0, limit - current)
        reset_time = int(time.time()) + 1

        return current <= limit, {
            "limit": limit,
            "remaining": remaining,
            "reset": reset_time
        }

    async def check_daily_requests(
            self,
            api_key_id: uuid.UUID,
            limit: int
    ) -> Tuple[bool, dict]:
        """检查日调用量限制"""
        today = datetime.now().strftime("%Y%m%d")
        key = f"rate_limit:daily:{api_key_id}:{today}"

        now = datetime.now()
        tomorrow_0 = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        expire_seconds = int((tomorrow_0 - now).total_seconds())

        async with self.redis.pipeline() as pipe:
            pipe.incr(key)
            pipe.expire(key, expire_seconds, nx=True)
            results = await pipe.execute()

        current = results[0]
        remaining = max(0, limit - current)
        reset_time = int(tomorrow_0.timestamp())

        return current <= limit, {
            "limit": limit,
            "remaining": remaining,
            "reset": reset_time
        }

    async def check_all_limits(
            self,
            api_key: ApiKey
    ) -> Tuple[bool, str, dict]:
        """
        检查所有限制
        Returns:
            (is_allowed, error_message, rate_limit_headers)
        """
        # Check QPS
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

        # Check daily requests
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

        # All checks passed
        headers = {
            "X-RateLimit-Limit-QPS": str(qps_info["limit"]),
            "X-RateLimit-Remaining-QPS": str(qps_info["remaining"]),
            "X-RateLimit-Limit-Day": str(daily_info["limit"]),
            "X-RateLimit-Remaining-Day": str(daily_info["remaining"]),
            "X-RateLimit-Reset": str(daily_info["reset"])
        }
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
        1. Key hash 是否存在
        2. is_active 是否为true
        3. expires_at 是否未过期
        4. quota 是否未超限
        """
        key_hash = hash_api_key(api_key)
        api_key_obj = ApiKeyRepository.get_by_hash(db, key_hash)

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
            resource_type: str,
            resource_id: uuid.UUID
    ) -> bool:
        """检查资源绑定"""
        if not api_key.resource_id:
            return True

        return (
                api_key.resource_type == resource_type and
                api_key.resource_id == resource_id
        )
