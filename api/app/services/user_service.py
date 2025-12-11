import datetime
import secrets
import string
from sqlalchemy.orm import Session
import uuid

from app.models.user_model import User
from app.repositories import user_repository
from app.schemas.user_schema import UserCreate
from app.schemas.tenant_schema import TenantCreate
from app.services.tenant_service import TenantService
from app.services.session_service import SessionService
from app.core.security import get_password_hash, verify_password
from app.core.config import settings
from app.core.logging_config import get_business_logger
from app.core.exceptions import BusinessException, PermissionDeniedException
from app.core.error_codes import BizCode
# from app.services import workspace_service
# from app.schemas.workspace_schema import WorkspaceCreate

# 获取业务逻辑专用日志器
business_logger = get_business_logger()


def create_initial_superuser(db: Session):
    business_logger.info("检查并创建初始超级用户")
    
    superuser = user_repository.get_superuser(db)
    if superuser:
        business_logger.info("超级用户已存在，跳过创建")
        return
    
    user_in = UserCreate(
            username=settings.FIRST_SUPERUSER_USERNAME,
            email=settings.FIRST_SUPERUSER_EMAIL,
            password=settings.FIRST_SUPERUSER_PASSWORD,
        )

    try:
        business_logger.debug("开始创建初始租户")
         # Create a default tenant for the superuser
        default_tenant = TenantCreate(
            name=f"{user_in.username}'s Tenant",
            description=f"Default tenant for {user_in.username}",
        )
        # Create tenant service and create tenant with user assignment
        tenant_service = TenantService(db)
        tenant =  tenant_service.create_tenant(default_tenant)
        db.flush()
        business_logger.debug("开始创建初始超级用户")
        
        hashed_password = get_password_hash(user_in.password)
        superuser = user_repository.create_user(
            db=db, user=user_in, hashed_password=hashed_password, is_superuser=True, 
            tenant_id=tenant.id
        )
        db.commit()
        db.refresh(superuser)
        business_logger.info(f"初始超级用户创建成功: {superuser.username} (ID: {superuser.id})")
        return superuser
    except Exception as e:
        business_logger.error(f"初始超级用户创建失败: {str(e)}")
        db.rollback()
        raise BusinessException(
            f"初始超级用户创建失败: {str(e)}", 
            code=BizCode.DB_ERROR,
            context={"username": user_in.username, "email": user_in.email},
            cause=e
        )


def create_user(db: Session, user: UserCreate) -> User:
    business_logger.info(f"创建用户: {user.username}, email: {user.email}")
    
    try:
        # 检查用户名是否已存在
        business_logger.debug(f"检查用户名是否已存在: {user.username}")
        db_user_by_username = user_repository.get_user_by_username(db, username=user.username)
        if db_user_by_username:
            business_logger.warning(f"用户名已存在: {user.username}")
            raise BusinessException(
                "用户名已存在", 
                code=BizCode.DUPLICATE_NAME,
                context={"username": user.username, "email": user.email}
            )
        
        # 检查邮箱是否已注册
        business_logger.debug(f"检查邮箱是否已注册: {user.email}")
        db_user_by_email = user_repository.get_user_by_email(db, email=user.email)
        if db_user_by_email:
            business_logger.warning(f"邮箱已注册: {user.email}")
            raise BusinessException(
                "邮箱已注册", 
                code=BizCode.DUPLICATE_NAME,
                context={"email": user.email, "username": user.username}
            )

        # 创建普通用户，需要有默认租户
        business_logger.debug(f"开始创建用户: {user.username}")
        hashed_password = get_password_hash(user.password)
        
        # 获取默认租户（第一个活跃租户）
        from app.repositories.tenant_repository import TenantRepository
        tenant_repo = TenantRepository(db)
        tenants = tenant_repo.get_tenants(skip=0, limit=1, is_active=True)
        
        if not tenants:
            business_logger.error("系统中没有可用的租户")
            raise BusinessException(
                "系统配置错误：没有可用的租户", 
                code=BizCode.TENANT_NOT_FOUND,
                context={"username": user.username, "email": user.email}
            )
        
        default_tenant = tenants[0]
        
        new_user = user_repository.create_user(
            db=db, user=user, hashed_password=hashed_password, 
            tenant_id=default_tenant.id, is_superuser=False
        )

        db.commit()
        db.refresh(new_user)
        business_logger.info(f"用户创建成功: {new_user.username} (ID: {new_user.id})")
        return new_user
    except Exception as e:
        business_logger.error(f"用户创建失败: {user.username} - {str(e)}")
        db.rollback()
        raise BusinessException(
            f"用户创建失败: {user.username} - {str(e)}", 
            code=BizCode.DB_ERROR,
            context={"username": user.username, "email": user.email},
            cause=e
        )


def create_superuser(db: Session, user: UserCreate, current_user: User) -> User:
    business_logger.info(f"创建超级管理员: {user.username}, email: {user.email}")
    
    # 检查当前用户是否为超级管理员
    from app.core.permissions import permission_service, Subject
    
    subject = Subject.from_user(current_user)
    try:
        permission_service.check_superuser(
            subject,
            error_message="只有超级管理员才能创建超级管理员用户"
        )
    except PermissionDeniedException as e:
        business_logger.warning(f"非超级管理员尝试创建超级管理员用户: {user.username}")
        raise BusinessException(
            str(e), 
            code=BizCode.FORBIDDEN,
            context={
                "current_user_id": str(current_user.id),
                "current_user_username": current_user.username,
                "target_username": user.username
            }
        )
    
    try:
        # 检查用户名是否已存在
        business_logger.debug(f"检查用户名是否已存在: {user.username}")
        db_user_by_username = user_repository.get_user_by_username(db, username=user.username)
        if db_user_by_username:
            business_logger.warning(f"用户名已存在: {user.username}")
            raise BusinessException(
                "用户名已存在", 
                code=BizCode.DUPLICATE_NAME,
                context={
                    "username": user.username,
                    "email": user.email,
                    "created_by": str(current_user.id)
                }
            )
        
        # 检查邮箱是否已注册
        business_logger.debug(f"检查邮箱是否已注册: {user.email}")
        db_user_by_email = user_repository.get_user_by_email(db, email=user.email)
        if db_user_by_email:
            business_logger.warning(f"邮箱已注册: {user.email}")
            raise BusinessException(
                "邮箱已注册", 
                code=BizCode.DUPLICATE_NAME,
                context={
                    "email": user.email, 
                    "username": user.username,
                    "created_by": str(current_user.id)
                }
            )

        # 创建超级管理员用户并加入当前用户的租户
        business_logger.debug(f"开始创建超级管理员: {user.username}")
        hashed_password = get_password_hash(user.password)
        
        new_user = user_repository.create_user(
            db=db, user=user, hashed_password=hashed_password, 
            tenant_id=current_user.tenant_id, is_superuser=True
        )

        db.commit()
        db.refresh(new_user)
        business_logger.info(f"超级管理员创建成功: {new_user.username} (ID: {new_user.id}), 已加入租户: {current_user.tenant_id}")
        return new_user
    except Exception as e:
        business_logger.error(f"超级管理员创建失败: {user.username} - {str(e)}")
        db.rollback()
        raise BusinessException(
            f"超级管理员创建失败: {user.username} - {str(e)}", 
            code=BizCode.DB_ERROR,
            context={
                "username": user.username, 
                "email": user.email,
                "created_by": str(current_user.id),
                "tenant_id": str(current_user.tenant_id)
            },
            cause=e
        )


def deactivate_user(db: Session, user_id_to_deactivate: uuid.UUID, current_user: User) -> User:
    business_logger.info(f"停用用户: user_id={user_id_to_deactivate}, 操作者: {current_user.username}")
    
    try:
        # 查找用户
        business_logger.debug(f"查找待停用用户: {user_id_to_deactivate}")
        db_user = user_repository.get_user_by_id(db, user_id=user_id_to_deactivate)
        if not db_user:
            business_logger.warning(f"用户不存在: {user_id_to_deactivate}")
            raise BusinessException(
                "用户不存在", 
                code=BizCode.USER_NOT_FOUND,
                context={"user_id": str(user_id_to_deactivate)}
            )
        
        # 权限检查 using permission service
        from app.core.permissions import permission_service, Subject, Resource, Action
        
        subject = Subject.from_user(current_user)
        resource = Resource.from_user(db_user)
        
        try:
            permission_service.require_permission(
                subject,
                Action.DEACTIVATE,
                resource,
                error_message="没有权限停用该用户"
            )
        except PermissionDeniedException as e:
            business_logger.warning(f"权限不足: 用户 {current_user.username} 尝试停用用户 {user_id_to_deactivate}")
            raise BusinessException(
                str(e), 
                code=BizCode.FORBIDDEN,
                context={
                    "current_user_id": str(current_user.id),
                    "current_user_username": current_user.username,
                    "target_user_id": str(user_id_to_deactivate)
                }
            )
        # 检查用户类型，如果是超级管理员，判断一下不是唯一的一个
        if db_user.is_superuser:
            is_only_superuser = user_repository.check_superuser_only(db)
            if is_only_superuser:
                business_logger.warning(f"停用超级管理员用户: {db_user.username} (ID: {user_id_to_deactivate})")
                raise BusinessException(
                    "不能停用唯一的超级管理员用户", 
                    code=BizCode.FORBIDDEN,
                    context={
                        "user_id": str(user_id_to_deactivate),
                        "username": db_user.username
                    }
                )

        # 停用用户
        business_logger.debug(f"执行用户停用: {db_user.username} (ID: {user_id_to_deactivate})")
        db_user.is_active = False
        db.add(db_user)
        db.commit()
        db.refresh(db_user)
        business_logger.info(f"用户停用成功: {db_user.username} (ID: {user_id_to_deactivate})")
        return db_user
    except Exception as e:
        business_logger.error(f"用户停用失败: user_id={user_id_to_deactivate} - {str(e)}")
        db.rollback()
        if isinstance(e, BusinessException):
            raise e
        raise BusinessException(f"{str(e)}", code=BizCode.DB_ERROR)

def activate_user(db: Session, user_id_to_activate: uuid.UUID, current_user: User) -> User:
    business_logger.info(f"激活用户: user_id={user_id_to_activate}, 操作者: {current_user.username}")
    
    try:
        # 查找用户
        business_logger.debug(f"查找待激活用户: {user_id_to_activate}")
        db_user = user_repository.get_user_by_id(db, user_id=user_id_to_activate)
        if not db_user:
            business_logger.warning(f"用户不存在: {user_id_to_activate}")
            raise BusinessException("用户不存在", code=BizCode.USER_NOT_FOUND)
        
        # 权限检查 using permission service
        from app.core.permissions import permission_service, Subject, Resource, Action
        
        subject = Subject.from_user(current_user)
        resource = Resource.from_user(db_user)
        
        try:
            permission_service.require_permission(
                subject,
                Action.ACTIVATE,
                resource,
                error_message="没有权限激活该用户"
            )
        except PermissionDeniedException as e:
            business_logger.warning(f"权限不足: 用户 {current_user.username} 尝试激活用户 {user_id_to_activate}")
            raise BusinessException(str(e), code=BizCode.FORBIDDEN)

        # 激活用户
        business_logger.debug(f"执行用户激活: {db_user.username} (ID: {user_id_to_activate})")
        db_user.is_active = True
        db.add(db_user)
        db.commit()
        db.refresh(db_user)
        business_logger.info(f"用户激活成功: {db_user.username} (ID: {user_id_to_activate})")
        return db_user
    except Exception as e:
        business_logger.error(f"用户激活失败: user_id={user_id_to_activate} - {str(e)}")
        db.rollback()
        raise BusinessException(f"用户激活失败: user_id={user_id_to_activate} - {str(e)}", code=BizCode.DB_ERROR)


def get_user(db: Session, user_id: uuid.UUID, current_user: User) -> User:
    business_logger.info(f"获取用户信息: user_id={user_id}, 操作者: {current_user.username}")
    
    try:
        # 查找用户
        business_logger.debug(f"查找用户: {user_id}")
        db_user = user_repository.get_user_by_id(db, user_id=user_id)
        if not db_user:
            business_logger.warning(f"用户不存在: {user_id}")
            raise BusinessException("用户不存在", code=BizCode.USER_NOT_FOUND)
        
        # 权限检查 using permission service
        from app.core.permissions import permission_service, Subject, Resource, Action
        
        subject = Subject.from_user(current_user)
        resource = Resource.from_user(db_user)
        
        try:
            permission_service.require_permission(
                subject,
                Action.READ,
                resource,
                error_message="没有权限获取该用户信息"
            )
        except PermissionDeniedException as e:
            business_logger.warning(f"权限不足: 用户 {current_user.username} 尝试获取用户 {user_id} 信息")
            raise BusinessException(str(e), code=BizCode.FORBIDDEN)

        # 返回用户信息
        business_logger.debug(f"返回用户信息: {db_user.username} (ID: {user_id})")
        return db_user
    except Exception as e:
        business_logger.error(f"获取用户信息失败: user_id={user_id} - {str(e)}")
        raise BusinessException(f"获取用户信息失败: user_id={user_id} - {str(e)}", code=BizCode.DB_ERROR)


def get_tenant_superusers(db: Session, current_user: User, include_inactive: bool = True) -> list[User]:
    """获取当前租户下的超管账号列表"""
    business_logger.info(f"获取租户超管列表: tenant_id={current_user.tenant_id}, 请求者: {current_user.username}, include_inactive={include_inactive}") 
    
    try:
        # 检查当前用户是否有权限查看（只有超管才能查看超管列表）
        from app.core.permissions import permission_service, Subject
        
        subject = Subject.from_user(current_user)
        try:
            permission_service.check_superuser(
                subject,
                error_message="只有超级管理员才能查看超管列表"
            )
        except PermissionDeniedException as e:
            business_logger.warning(f"非超级管理员尝试查看超管列表: {current_user.username}")
            raise BusinessException(str(e), code=BizCode.FORBIDDEN)
        
        # 检查用户是否有租户
        if not current_user.tenant_id:
            business_logger.warning(f"用户没有租户信息: {current_user.username}")
            raise BusinessException("用户没有租户信息", code=BizCode.TENANT_NOT_FOUND)
        
        # 获取租户下的超管列表
        business_logger.debug(f"查询租户超管: tenant_id={current_user.tenant_id}, include_inactive={include_inactive}")
        is_active_filter = None if include_inactive else True
        superusers = user_repository.get_superusers_by_tenant(
            db=db, 
            tenant_id=current_user.tenant_id, 
            is_active=is_active_filter
        )
        
        business_logger.info(f"租户超管查询成功: tenant_id={current_user.tenant_id}, count={len(superusers)}")
        return superusers
        
    except Exception as e:
        business_logger.error(f"获取租户超管列表失败: tenant_id={current_user.tenant_id} - {str(e)}")
        raise BusinessException(f"获取租户超管列表失败: tenant_id={current_user.tenant_id} - {str(e)}", code=BizCode.DB_ERROR)


def update_last_login_time(db: Session, user_id: uuid.UUID) -> User:
    """更新用户的最后登录时间"""
    business_logger.info(f"更新用户最后登录时间: user_id={user_id}")
    
    try:
        # 获取用户
        db_user = user_repository.get_user_by_id(db=db, user_id=user_id)
        if not db_user:
            business_logger.warning(f"用户不存在: {user_id}")
            raise BusinessException("用户不存在", code=BizCode.USER_NOT_FOUND)
        
        # 更新最后登录时间
        db_user.last_login_at = datetime.datetime.now()
        db.commit()
        db.refresh(db_user)
        
        business_logger.info(f"用户最后登录时间更新成功: {db_user.username} (ID: {user_id})")
        return db_user
        
    except (BusinessException, PermissionDeniedException):
        raise
    except Exception as e:
        business_logger.error(f"更新用户最后登录时间失败: user_id={user_id} - {str(e)}")
        db.rollback()
        raise


async def change_password(db: Session, user_id: uuid.UUID, old_password: str, new_password: str, current_user: User) -> User:
    """普通用户修改自己的密码"""
    business_logger.info(f"用户修改密码请求: user_id={user_id}, current_user={current_user.id}")
    
    # 检查权限：只能修改自己的密码
    if current_user.id != user_id:
        business_logger.warning(f"用户尝试修改他人密码: current_user={current_user.id}, target_user={user_id}")
        raise PermissionDeniedException("You can only change your own password")
    
    try:
        # 获取用户
        db_user = user_repository.get_user_by_id(db=db, user_id=user_id)
        if not db_user:
            business_logger.warning(f"用户不存在: {user_id}")
            raise BusinessException("User not found", code=BizCode.USER_NOT_FOUND)
        
        # 验证旧密码
        if not verify_password(old_password, db_user.hashed_password):
            business_logger.warning(f"用户旧密码验证失败: {user_id}")
            raise BusinessException("当前密码不正确", code=BizCode.VALIDATION_FAILED)
        
        # 更新密码
        db_user.hashed_password = get_password_hash(new_password)
        db.commit()
        db.refresh(db_user)
        
        # 使所有旧 tokens 失效
        await SessionService.invalidate_all_user_tokens(str(user_id))
        
        business_logger.info(f"用户密码修改成功: {db_user.username} (ID: {user_id})")
        return db_user
        
    except Exception as e:
        business_logger.error(f"修改用户密码失败: user_id={user_id} - {str(e)}")
        db.rollback()
        raise BusinessException(f"修改用户密码失败: user_id={user_id} - {str(e)}", code=BizCode.DB_ERROR)


async def admin_change_password(db: Session, target_user_id: uuid.UUID, new_password: str = None, current_user: User = None) -> tuple[User, str]:
    """
    超级管理员修改指定用户的密码
    
    Args:
        db: 数据库会话
        target_user_id: 目标用户ID
        new_password: 新密码，如果为None则自动生成随机密码
        current_user: 当前用户（超级管理员）
        
    Returns:
        tuple[User, str]: (更新后的用户对象, 实际使用的密码)
    """
    business_logger.info(f"管理员修改用户密码请求: admin={current_user.id}, target_user={target_user_id}")
    
    # 检查权限：只有超级管理员可以修改他人密码
    from app.core.permissions import permission_service, Subject
    
    subject = Subject.from_user(current_user)
    try:
        permission_service.check_superuser(
            subject,
            error_message="只有超级管理员可以修改他人密码"
        )
    except PermissionDeniedException as e:
        business_logger.warning(f"非超管用户尝试修改他人密码: current_user={current_user.id}")
        raise BusinessException(str(e), code=BizCode.FORBIDDEN)
    
    try:
        # 获取目标用户
        target_user = user_repository.get_user_by_id(db=db, user_id=target_user_id)
        if not target_user:
            business_logger.warning(f"目标用户不存在: {target_user_id}")
            raise BusinessException("目标用户不存在", code=BizCode.USER_NOT_FOUND)
        
        # 检查租户权限：超管只能修改同租户用户的密码
        if current_user.tenant_id != target_user.tenant_id:
            business_logger.warning(f"跨租户密码修改尝试: admin_tenant={current_user.tenant_id}, target_tenant={target_user.tenant_id}")
            raise BusinessException("不可跨租户修改用户密码", code=BizCode.FORBIDDEN)
        
        # 如果没有提供新密码，则生成随机密码
        actual_password = new_password if new_password else generate_random_password()
        
        # 更新密码
        target_user.hashed_password = get_password_hash(actual_password)
        db.commit()
        db.refresh(target_user)
        
        # 使所有旧 tokens 失效
        await SessionService.invalidate_all_user_tokens(str(target_user_id))
        
        password_type = "指定密码" if new_password else "随机生成密码"
        business_logger.info(f"管理员修改用户密码成功: admin={current_user.username}, target={target_user.username} (ID: {target_user_id}), 类型={password_type}")
        return target_user, actual_password
        
    except Exception as e:
        business_logger.error(f"管理员修改用户密码失败: admin={current_user.id}, target_user={target_user_id} - {str(e)}")
        db.rollback()
        raise BusinessException(f"管理员修改用户密码失败: admin={current_user.id}, target_user={target_user_id} - {str(e)}", code=BizCode.DB_ERROR)


def generate_random_password(length: int = 12) -> str:
    """
    生成随机密码
    
    Args:
        length: 密码长度，默认12位
        
    Returns:
        str: 生成的随机密码
    """
    # 确保密码包含大小写字母、数字和特殊字符
    lowercase = string.ascii_lowercase
    uppercase = string.ascii_uppercase
    digits = string.digits
    special_chars = "!@#$%^&*"
    
    # 确保至少包含每种字符类型
    password = [
        secrets.choice(lowercase),
        secrets.choice(uppercase),
        secrets.choice(digits),
        secrets.choice(special_chars)
    ]
    
    # 填充剩余长度
    all_chars = lowercase + uppercase + digits + special_chars
    for _ in range(length - 4):
        password.append(secrets.choice(all_chars))
    
    # 打乱顺序
    secrets.SystemRandom().shuffle(password)
    
    return ''.join(password)
