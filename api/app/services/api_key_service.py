"""API Key Service"""
from sqlalchemy.orm import Session
from typing import Optional, Tuple, List
import uuid
import datetime
import math

from app.models.api_key_model import ApiKey, ApiKeyType
from app.repositories.api_key_repository import ApiKeyRepository
from app.schemas import api_key_schema
from app.schemas.response_schema import PageData, PageMeta
from app.core.api_key_utils import generate_api_key
from app.core.exceptions import BusinessException
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
        """创建 API Key
        
        Returns:
            Tuple[ApiKey, str]: (API Key 对象, API Key 明文)
        """
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
            "rate_limit": data.rate_limit,
            "quota_limit": data.quota_limit,
            "expires_at": data.expires_at,
            "created_by": user_id,
            "created_at": datetime.datetime.now(),
            "updated_at": datetime.datetime.now(),
        }
        
        api_key_obj = ApiKeyRepository.create(db, api_key_data)
        db.commit()
        db.refresh(api_key_obj)
        
        logger.info(f"API Key 创建成功", extra={
            "api_key_id": str(api_key_obj.id),
            "name": data.name,
            "type": data.type
        })
        
        return api_key_obj, api_key
    
    @staticmethod
    def get_api_key(
        db: Session,
        api_key_id: uuid.UUID,
        workspace_id: uuid.UUID
    ) -> ApiKey:
        """获取 API Key"""
        api_key = ApiKeyRepository.get_by_id(db, api_key_id)
        if not api_key:
            raise BusinessException("API Key 不存在", BizCode.NOT_FOUND)
        
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
        """更新 API Key"""
        api_key = ApiKeyService.get_api_key(db, api_key_id, workspace_id)
        
        update_data = data.model_dump(exclude_unset=True)
        ApiKeyRepository.update(db, api_key_id, update_data)
        db.commit()
        db.refresh(api_key)
        
        logger.info(f"API Key 更新成功", extra={"api_key_id": str(api_key_id)})
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
        
        logger.info(f"API Key 删除成功", extra={"api_key_id": str(api_key_id)})
        return True
    
    @staticmethod
    def regenerate_api_key(
        db: Session,
        api_key_id: uuid.UUID,
        workspace_id: uuid.UUID
    ) -> Tuple[ApiKey, str]:
        """重新生成 API Key"""
        api_key = ApiKeyService.get_api_key(db, api_key_id, workspace_id)
        
        # 生成新的 API Key
        new_api_key, key_hash, key_prefix = generate_api_key(ApiKeyType(api_key.type))
        
        # 更新
        ApiKeyRepository.update(db, api_key_id, {
            "key_hash": key_hash,
            "key_prefix": key_prefix
        })
        db.commit()
        db.refresh(api_key)
        
        logger.info(f"API Key 重新生成成功", extra={"api_key_id": str(api_key_id)})
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
