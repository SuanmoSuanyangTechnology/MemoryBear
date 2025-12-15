from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, or_, func
from typing import List, Optional
import uuid

from app.models.user_model import User
from app.models.tenant_model import Tenants
from app.schemas.user_schema import UserCreate, UserUpdate
from app.core.logging_config import get_db_logger

# 获取数据库专用日志器
db_logger = get_db_logger()


class UserRepository:
    """用户数据访问层"""

    def __init__(self, db: Session):
        self.db = db

    def get_user_by_id(self, user_id: uuid.UUID) -> Optional[User]:
        """根据ID获取用户"""
        db_logger.debug(f"根据ID查询用户: user_id={user_id}")
        
        try:
            user = self.db.query(User).options(joinedload(User.tenant)).filter(User.id == user_id).first()
            if user:
                db_logger.debug(f"用户查询成功: {user.username} (ID: {user_id})")
            else:
                db_logger.debug(f"用户不存在: user_id={user_id}")
            return user
        except Exception as e:
            db_logger.error(f"根据ID查询用户失败: user_id={user_id} - {str(e)}")
            raise

    def get_user_by_email(self, email: str) -> Optional[User]:
        """根据邮箱获取用户"""
        db_logger.debug(f"根据邮箱查询用户: email={email}")
        
        try:
            user = self.db.query(User).options(joinedload(User.tenant)).filter(User.email == email).first()
            if user:
                db_logger.debug(f"用户查询成功: {user.username} (email: {email})")
            else:
                db_logger.debug(f"用户不存在: email={email}")
            return user
        except Exception as e:
            db_logger.error(f"根据邮箱查询用户失败: email={email} - {str(e)}")
            raise

    def get_user_by_username(self, username: str) -> Optional[User]:
        """根据用户名获取用户"""
        db_logger.debug(f"根据用户名查询用户: username={username}")
        
        try:
            user = self.db.query(User).options(joinedload(User.tenant)).filter(User.username == username).first()
            if user:
                db_logger.debug(f"用户查询成功: {user.username} (ID: {user.id})")
            else:
                db_logger.debug(f"用户不存在: username={username}")
            return user
        except Exception as e:
            db_logger.error(f"根据用户名查询用户失败: username={username} - {str(e)}")
            raise

    def get_superuser(self) -> Optional[User]:
        """获取超级用户"""
        db_logger.debug("查询超级用户")
        
        try:
            user = self.db.query(User).options(joinedload(User.tenant)).filter(User.is_active == True).filter(User.is_superuser == True).first()
            if user:
                db_logger.debug(f"超级用户查询成功: {user.username}")
            else:
                db_logger.debug("超级用户不存在")
            return user
        except Exception as e:
            db_logger.error(f"查询超级用户失败: {str(e)}")
            raise
    def check_superuser_only(self) -> bool:
        """检查是否只有一个超级用户"""
        db_logger.debug("检查是否只有一个超级用户")
        
        try:
            count = self.db.query(User).options(joinedload(User.tenant)).filter(User.is_active == True).filter(User.is_superuser == True).count()
            return count == 1
        except Exception as e:
            db_logger.error(f"检查超级用户数量失败: {str(e)}")
            raise

    def create_user(
        self, 
        user_data: UserCreate, 
        hashed_password: str, 
        tenant_id: Optional[uuid.UUID] = None,
        is_superuser: bool = False
    ) -> User:
        """创建用户"""
        db_logger.debug(f"创建用户记录: username={user_data.username}, email={user_data.email}, is_superuser={is_superuser}")
        
        try:
            db_user = User(
                username=user_data.username,
                email=user_data.email,
                hashed_password=hashed_password,
                tenant_id=tenant_id,
                is_superuser=is_superuser,
            )
            self.db.add(db_user)
            self.db.flush()
            db_logger.info(f"用户记录创建成功: {user_data.username} (email: {user_data.email})")
            return db_user
        except Exception as e:
            db_logger.error(f"创建用户记录失败: username={user_data.username} - {str(e)}")
            raise

    def update_user(self, user_id: uuid.UUID, user_data: UserUpdate) -> Optional[User]:
        """更新用户"""
        db_logger.debug(f"更新用户: user_id={user_id}")
        
        try:
            user = self.get_user_by_id(user_id)
            if not user:
                db_logger.debug(f"用户不存在: user_id={user_id}")
                return None
            
            for field, value in user_data.dict(exclude_unset=True).items():
                setattr(user, field, value)
            
            self.db.flush()
            db_logger.info(f"用户更新成功: {user.username}")
            return user
        except Exception as e:
            db_logger.error(f"更新用户失败: user_id={user_id} - {str(e)}")
            raise

    def delete_user(self, user_id: uuid.UUID) -> bool:
        """删除用户"""
        db_logger.debug(f"删除用户: user_id={user_id}")
        
        try:
            user = self.get_user_by_id(user_id)
            if not user:
                db_logger.debug(f"用户不存在: user_id={user_id}")
                return False
            
            # 逻辑删除用户
            user.is_active = False
            self.db.flush()
            db_logger.info(f"用户删除成功（逻辑删除）: {user.username}")
            return True
        except Exception as e:
            db_logger.error(f"删除用户失败: user_id={user_id} - {str(e)}")
            raise

    def get_users_by_tenant(
        self, 
        tenant_id: uuid.UUID, 
        skip: int = 0, 
        limit: int = 100,
        is_active: Optional[bool] = None,
        search: Optional[str] = None
    ) -> List[User]:
        """获取租户下的用户列表"""
        db_logger.debug(f"查询租户用户: tenant_id={tenant_id}")
        
        try:
            query = self.db.query(User).options(joinedload(User.tenant)).filter(User.tenant_id == tenant_id)
            
            if is_active is not None:
                query = query.filter(User.is_active == is_active)
            
            if search:
                query = query.filter(
                    or_(
                        User.username.ilike(f"%{search}%"),
                        User.email.ilike(f"%{search}%")
                    )
                )
            
            users = query.offset(skip).limit(limit).all()
            db_logger.debug(f"租户用户查询成功: tenant_id={tenant_id}, count={len(users)}")
            return users
        except Exception as e:
            db_logger.error(f"查询租户用户失败: tenant_id={tenant_id} - {str(e)}")
            raise

    def count_users_by_tenant(
        self, 
        tenant_id: uuid.UUID,
        is_active: Optional[bool] = None,
        search: Optional[str] = None
    ) -> int:
        """统计租户下的用户数量"""
        try:
            query = self.db.query(func.count(User.id)).filter(User.tenant_id == tenant_id)
            
            if is_active is not None:
                query = query.filter(User.is_active == is_active)
            
            if search:
                query = query.filter(
                    or_(
                        User.username.ilike(f"%{search}%"),
                        User.email.ilike(f"%{search}%")
                    )
                )
            
            return query.scalar()
        except Exception as e:
            db_logger.error(f"统计租户用户失败: tenant_id={tenant_id} - {str(e)}")
            raise

    def get_superusers_by_tenant(
        self, 
        tenant_id: uuid.UUID, 
        is_active: Optional[bool] = True
    ) -> List[User]:
        """获取租户下的超管用户列表"""
        db_logger.debug(f"查询租户超管用户: tenant_id={tenant_id}")
        
        try:
            query = self.db.query(User).options(joinedload(User.tenant)).filter(
                and_(
                    User.tenant_id == tenant_id,
                    User.is_superuser == True
                )
            )
            
            if is_active is not None:
                query = query.filter(User.is_active == is_active)
            
            users = query.all()
            db_logger.debug(f"租户超管用户查询成功: tenant_id={tenant_id}, count={len(users)}")
            return users
        except Exception as e:
            db_logger.error(f"查询租户超管用户失败: tenant_id={tenant_id} - {str(e)}")
            raise

    def assign_user_to_tenant(self, user_id: uuid.UUID, tenant_id: uuid.UUID) -> bool:
        """将用户分配给租户"""
        db_logger.debug(f"分配用户到租户: user_id={user_id}, tenant_id={tenant_id}")
        
        try:
            user = self.get_user_by_id(user_id)
            if not user:
                db_logger.debug(f"用户不存在: user_id={user_id}")
                return False
            
            # 验证租户存在
            tenant = self.db.query(Tenants).filter(Tenants.id == tenant_id).first()
            if not tenant:
                db_logger.debug(f"租户不存在: tenant_id={tenant_id}")
                return False
            
            user.tenant_id = tenant_id
            self.db.flush()
            db_logger.info(f"用户分配成功: user={user.username}, tenant={tenant.name}")
            return True
        except Exception as e:
            db_logger.error(f"分配用户到租户失败: user_id={user_id}, tenant_id={tenant_id} - {str(e)}")
            raise

    def get_users_without_tenant(
        self, 
        skip: int = 0, 
        limit: int = 100,
        is_active: Optional[bool] = None
    ) -> List[User]:
        """获取没有租户的用户列表"""
        try:
            query = self.db.query(User).filter(User.tenant_id.is_(None))
            
            if is_active is not None:
                query = query.filter(User.is_active == is_active)
            
            return query.offset(skip).limit(limit).all()
        except Exception as e:
            db_logger.error(f"查询无租户用户失败: {str(e)}")
            raise


# 便利函数，保持向后兼容
def get_user_by_id(db: Session, user_id: uuid.UUID) -> Optional[User]:
    """根据ID获取用户"""
    return UserRepository(db).get_user_by_id(user_id)

def get_user_by_email(db: Session, email: str) -> Optional[User]:
    """根据邮箱获取用户"""
    return UserRepository(db).get_user_by_email(email)

def get_user_by_username(db: Session, username: str) -> Optional[User]:
    """根据用户名获取用户"""
    return UserRepository(db).get_user_by_username(username)

def get_superuser(db: Session) -> Optional[User]:
    """获取超级用户"""
    return UserRepository(db).get_superuser()

def check_superuser_only(db: Session) -> Optional[User]:
    """检查是否只有一个超级用户"""
    return UserRepository(db).check_superuser_only()

def create_user(
    db: Session, 
    user: UserCreate, 
    hashed_password: str, 
    tenant_id: Optional[uuid.UUID] = None,
    is_superuser: bool = False
) -> User:
    """创建用户（函数式接口）"""
    repo = UserRepository(db)
    return repo.create_user(user, hashed_password, tenant_id, is_superuser)


def get_superusers_by_tenant(
    db: Session, 
    tenant_id: uuid.UUID, 
    is_active: Optional[bool] = True
) -> List[User]:
    """获取租户下的超管用户列表（函数式接口）"""
    repo = UserRepository(db)
    return repo.get_superusers_by_tenant(tenant_id, is_active)
