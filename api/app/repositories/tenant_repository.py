import uuid
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, or_, func
from typing import List, Optional

from app.models.tenant_model import Tenants
from app.models.user_model import User
from app.schemas.tenant_schema import TenantCreate, TenantUpdate


class TenantRepository:
    """租户数据访问层"""

    def __init__(self, db: Session):
        self.db = db

    def create_tenant(self, tenant_data: TenantCreate) -> Tenants:
        """创建租户"""
        db_tenant = Tenants(
            name=tenant_data.name,
            id=uuid.uuid4(),
            description=tenant_data.description,
            is_active=tenant_data.is_active
        )
        self.db.add(db_tenant)
        self.db.flush()
        return db_tenant

    def get_tenant_by_id(self, tenant_id: uuid.UUID) -> Optional[Tenants]:
        """根据ID获取租户"""
        return self.db.query(Tenants).filter(Tenants.id == tenant_id).first()

    def get_tenant_by_name(self, name: str) -> Optional[Tenants]:
        """根据名称获取租户"""
        return self.db.query(Tenants).filter(Tenants.name == name).first()

    def get_tenants(
        self,
        skip: int = 0,
        limit: int = 100,
        is_active: Optional[bool] = None,
        search: Optional[str] = None
    ) -> List[Tenants]:
        """获取租户列表"""
        query = self.db.query(Tenants)
        
        if is_active is not None:
            query = query.filter(Tenants.is_active == is_active)
        
        if search:
            query = query.filter(
                or_(
                    Tenants.name.ilike(f"%{search}%"),
                    Tenants.description.ilike(f"%{search}%")
                )
            )
        
        return query.offset(skip).limit(limit).all()

    def count_tenants(
        self,
        is_active: Optional[bool] = None,
        search: Optional[str] = None
    ) -> int:
        """统计租户数量"""
        query = self.db.query(func.count(Tenants.id))
        
        if is_active is not None:
            query = query.filter(Tenants.is_active == is_active)
        
        if search:
            query = query.filter(
                or_(
                    Tenants.name.ilike(f"%{search}%"),
                    Tenants.description.ilike(f"%{search}%")
                )
            )
        
        return query.scalar()

    def update_tenant(self, tenant_id: uuid.UUID, tenant_data: TenantUpdate) -> Optional[Tenants]:
        """更新租户"""
        db_tenant = self.get_tenant_by_id(tenant_id)
        if not db_tenant:
            return None
        
        for field, value in tenant_data.dict(exclude_unset=True).items():
            setattr(db_tenant, field, value)
        
        self.db.flush()
        return db_tenant

    def delete_tenant(self, tenant_id: uuid.UUID) -> bool:
        """逻辑删除租户"""
        db_tenant = self.get_tenant_by_id(tenant_id)
        if not db_tenant:
            return False
        
        # 逻辑删除租户
        db_tenant.is_active = False
        return True

    def get_tenant_users(self, tenant_id: uuid.UUID, is_active: Optional[bool] = None) -> List[User]:
        """获取租户下的所有用户"""
        query = self.db.query(User).filter(User.tenant_id == tenant_id)
        
        if is_active is not None:
            query = query.filter(User.is_active == is_active)
        
        return query.all()

    def get_user_tenant(self, user_id: uuid.UUID) -> Optional[Tenants]:
        """获取用户所属的租户"""
        user = self.db.query(User).filter(User.id == user_id).first()
        if not user or not user.tenant_id:
            return None
        
        return self.get_tenant_by_id(user.tenant_id)

    def assign_user_to_tenant(self, user_id: uuid.UUID, tenant_id: uuid.UUID) -> bool:
        """将用户分配给租户"""
        user = self.db.query(User).filter(User.id == user_id).first()
        if not user:
            return False
        
        # 验证租户存在
        tenant = self.get_tenant_by_id(tenant_id)
        if not tenant:
            return False
        
        user.tenant_id = tenant_id
        self.db.flush()
        return True

    def count_tenant_users(self, tenant_id: uuid.UUID, is_active: Optional[bool] = None) -> int:
        """统计租户下的用户数量"""
        query = self.db.query(func.count(User.id)).filter(User.tenant_id == tenant_id)
        
        if is_active is not None:
            query = query.filter(User.is_active == is_active)
        
        return query.scalar()


# 便利函数，保持向后兼容
def create_tenant(db: Session, tenant_data: TenantCreate) -> Tenants:
    """创建租户"""
    return TenantRepository(db).create_tenant(tenant_data)

def get_tenant_by_id(db: Session, tenant_id: uuid.UUID) -> Optional[Tenants]:
    """根据ID获取租户"""
    return TenantRepository(db).get_tenant_by_id(tenant_id)

def get_tenant_by_name(db: Session, name: str) -> Optional[Tenants]:
    """根据名称获取租户"""
    return TenantRepository(db).get_tenant_by_name(name)

def get_tenants(db: Session, skip: int = 0, limit: int = 100) -> List[Tenants]:
    """获取租户列表"""
    return TenantRepository(db).get_tenants(skip=skip, limit=limit)

def get_user_tenant(db: Session, user_id: uuid.UUID) -> Optional[Tenants]:
    """获取用户所属的租户"""
    return TenantRepository(db).get_user_tenant(user_id)

def get_tenant_users(db: Session, tenant_id: uuid.UUID) -> List[User]:
    """获取租户下的所有用户"""
    return TenantRepository(db).get_tenant_users(tenant_id)