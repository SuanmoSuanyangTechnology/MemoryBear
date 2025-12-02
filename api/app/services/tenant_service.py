from sqlalchemy.orm import Session
from typing import List, Optional
import uuid

from app.core.logging_config import get_business_logger
from app.repositories.tenant_repository import TenantRepository
from app.repositories.user_repository import UserRepository
from app.repositories.workspace_repository import WorkspaceRepository
from app.schemas.tenant_schema import (
    TenantCreate, TenantUpdate, Tenant, TenantQuery, TenantList
)
from app.schemas.user_schema import User
from app.schemas.workspace_schema import WorkspaceCreate
from app.models.tenant_model import Tenants
from app.models.user_model import User as UserModel
from app.core.exceptions import BusinessException
from app.core.error_codes import BizCode

# 获取业务逻辑专用日志器
business_logger = get_business_logger()

class TenantService:
    """租户业务逻辑层"""

    def __init__(self, db: Session):
        self.db = db
        self.tenant_repo = TenantRepository(db)
        self.user_repo = UserRepository(db)
        self.workspace_repo = WorkspaceRepository(db)

    def create_tenant(self, tenant_data: TenantCreate) -> Tenants:
        """创建租户"""
        # 检查租户名称是否已存在
        existing_tenant = self.tenant_repo.get_tenant_by_name(tenant_data.name)
        if existing_tenant:
            raise BusinessException(f"租户名称 '{tenant_data.name}' 已存在", code=BizCode.DUPLICATE_NAME)
        
        try:
            tenant = self.tenant_repo.create_tenant(tenant_data)
            business_logger.info(f"创建租户成功: {tenant.name} (ID: {tenant.id})")
            return tenant
        except Exception as e:
            business_logger.error(f"创建租户失败: {str(e)}")
            raise BusinessException(f"创建租户失败: {str(e)}", code=BizCode.DB_ERROR)

    def create_tenant_and_assign_user(self, tenant_data: TenantCreate, user_id: uuid.UUID) -> Tenants:
        """创建租户并分配用户"""
        try:
            # 创建租户
            tenant = self.create_tenant(tenant_data)
            
            # 将用户分配给租户
            success = self.user_repo.assign_user_to_tenant(user_id, tenant.id)
            if not success:
                raise BusinessException("分配用户到租户失败", code=BizCode.STATE_CONFLICT)
            
            business_logger.info(f"创建租户并分配用户成功: {tenant.name}")
            return tenant
            
        except Exception as e:
            business_logger.error(f"创建租户和分配用户失败: {str(e)}")
            self.db.rollback()
            raise BusinessException(f"创建租户失败: {str(e)}", code=BizCode.DB_ERROR)

    def get_tenant(self, tenant_id: uuid.UUID) -> Optional[Tenants]:
        """获取租户"""
        return self.tenant_repo.get_tenant_by_id(tenant_id)

    def get_tenant_by_name(self, name: str) -> Optional[Tenants]:
        """根据名称获取租户"""
        return self.tenant_repo.get_tenant_by_name(name)

    def get_tenants(self, query: TenantQuery) -> TenantList:
        """获取租户列表"""
        skip = (query.page - 1) * query.size
        
        tenants = self.tenant_repo.get_tenants(
            skip=skip,
            limit=query.size,
            is_active=query.is_active,
            search=query.search
        )
        
        total = self.tenant_repo.count_tenants(
            is_active=query.is_active,
            search=query.search
        )
        
        pages = (total + query.size - 1) // query.size
        
        return TenantList(
            items=[Tenant.model_validate(tenant) for tenant in tenants],
            total=total,
            page=query.page,
            size=query.size,
            pages=pages
        )

    def update_tenant(self, tenant_id: uuid.UUID, tenant_data: TenantUpdate) -> Optional[Tenants]:
        """更新租户"""
        # 如果更新名称，检查是否重复
        if tenant_data.name:
            existing_tenant = self.tenant_repo.get_tenant_by_name(tenant_data.name)
            if existing_tenant and existing_tenant.id != tenant_id:
                raise BusinessException(f"租户名称 '{tenant_data.name}' 已存在", code=BizCode.DUPLICATE_NAME)
        
        try:
            tenant = self.tenant_repo.update_tenant(tenant_id, tenant_data)
            if tenant:
                business_logger.info(f"更新租户成功: {tenant.name} (ID: {tenant.id})")
            return tenant
        except Exception as e:
            business_logger.error(f"更新租户失败: {str(e)}")
            raise BusinessException(f"更新租户失败: {str(e)}", code=BizCode.DB_ERROR)

    def delete_tenant(self, tenant_id: uuid.UUID) -> bool:
        """删除租户"""
        try:
            # 检查租户是否存在
            tenant = self.tenant_repo.get_tenant_by_id(tenant_id)
            if not tenant:
                return False
            
            # 检查是否有关联的用户
            users = self.tenant_repo.get_tenant_users(tenant_id)
            if users:
                raise BusinessException("无法删除租户，存在关联的用户", code=BizCode.STATE_CONFLICT)
            
            # 检查是否有关联的工作空间
            workspaces = self.workspace_repo.get_workspaces_by_tenant(tenant_id)
            if workspaces:
                raise BusinessException("无法删除租户，存在关联的工作空间", code=BizCode.STATE_CONFLICT)
            
            success = self.tenant_repo.delete_tenant(tenant_id)
            if success:
                business_logger.info(f"删除租户成功: {tenant.name} (ID: {tenant.id})")
            return success
            
        except Exception as e:
            business_logger.error(f"删除租户失败: {str(e)}")
            raise BusinessException(f"删除租户失败: {str(e)}", code=BizCode.DB_ERROR)

    # 租户用户管理
    def get_tenant_users(
        self, 
        tenant_id: uuid.UUID, 
        skip: int = 0, 
        limit: int = 100,
        is_active: Optional[bool] = None,
        search: Optional[str] = None
    ) -> List[UserModel]:
        """获取租户下的用户列表"""
        return self.user_repo.get_users_by_tenant(
            tenant_id=tenant_id,
            skip=skip,
            limit=limit,
            is_active=is_active,
            search=search
        )

    def count_tenant_users(
        self, 
        tenant_id: uuid.UUID,
        is_active: Optional[bool] = None,
        search: Optional[str] = None
    ) -> int:
        """统计租户下的用户数量"""
        return self.user_repo.count_users_by_tenant(
            tenant_id=tenant_id,
            is_active=is_active,
            search=search
        )

    def assign_user_to_tenant(self, user_id: uuid.UUID, tenant_id: uuid.UUID) -> bool:
        """将用户分配给租户"""
        # 检查租户是否存在
        tenant = self.tenant_repo.get_tenant_by_id(tenant_id)
        if not tenant:
            raise BusinessException("租户不存在", code=BizCode.TENANT_NOT_FOUND)
        
        try:
            success = self.user_repo.assign_user_to_tenant(user_id, tenant_id)
            if success:
                business_logger.info(f"分配用户到租户成功: 用户ID {user_id}, 租户ID {tenant_id}")
            return success
        except Exception as e:
            business_logger.error(f"分配用户到租户失败: {str(e)}")
            raise BusinessException(f"分配用户到租户失败: {str(e)}", code=BizCode.DB_ERROR)

    def get_user_tenant(self, user_id: uuid.UUID) -> Optional[Tenants]:
        """获取用户所属的租户"""
        return self.tenant_repo.get_user_tenant(user_id)

    def remove_user_from_tenant(self, user_id: uuid.UUID) -> bool:
        """将用户从租户中移除（设置tenant_id为None）"""
        try:
            user = self.user_repo.get_user_by_id(user_id)
            if not user:
                return False
            
            success = self.user_repo.assign_user_to_tenant(user_id, None)
            if success:
                business_logger.info(f"移除用户租户关联成功: 用户ID {user_id}")
            return success
        except Exception as e:
            business_logger.error(f"移除用户租户关联失败: {str(e)}")
            raise BusinessException(f"移除用户租户关联失败: {str(e)}", code=BizCode.DB_ERROR)

    def get_users_without_tenant(
        self, 
        skip: int = 0, 
        limit: int = 100,
        is_active: Optional[bool] = None
    ) -> List[UserModel]:
        """获取没有租户的用户列表"""
        return self.user_repo.get_users_without_tenant(
            skip=skip,
            limit=limit,
            is_active=is_active
        )