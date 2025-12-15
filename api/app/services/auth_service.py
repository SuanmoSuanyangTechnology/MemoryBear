from sqlalchemy.orm import Session
from typing import Optional, Tuple, Union
import jwt
import time

from app.models.user_model import User
from app.repositories import user_repository
from app.core.security import verify_password
from app.core.config import settings
from app.core.exceptions import BusinessException
from app.core.error_codes import BizCode

# Token 配置
TOKEN_SECRET_KEY = settings.SECRET_KEY
TOKEN_ALGORITHM = "HS256"

def authenticate_user(db: Session, email: str, password: str) -> Optional[User]:
    """
    Authenticates a user.

    :param db: The database session.
    :param email: The email.
    :param password: The password.
    :return: The user object if authentication is successful, otherwise None.
    """
    user = user_repository.get_user_by_email(db, email=email)
    if not user:
        return None  # User not found
    if not user.is_active:
        return None  # User is inactive
    if not verify_password(password, user.hashed_password):
        return None  # Incorrect password
    return user  # Authentication successful


def authenticate_user_with_status(db: Session, email: str, password: str) -> Tuple[bool, Optional[User], str]:
    """
    认证用户并返回详细状态（用于需要区分不同失败原因的场景）
    
    :param db: 数据库会话
    :param email: 用户邮箱
    :param password: 用户密码
    :return: (认证成功, 用户对象, 状态消息)
             状态消息: "success", "user_not_found", "user_inactive", "password_incorrect"
    """
    from app.core.logging_config import get_auth_logger
    
    logger = get_auth_logger()
    
    # 查找用户
    user = user_repository.get_user_by_email(db, email=email)
    if not user:
        logger.warning(f"用户不存在: {email}")
        return (False, None, "user_not_found")
    
    # 检查用户状态
    if not user.is_active:
        logger.warning(f"用户未激活: {email}")
        return (False, user, "user_inactive")
    
    # 验证密码
    if not verify_password(password, user.hashed_password):
        logger.warning(f"密码错误: {email}")
        return (False, user, "password_incorrect")
    
    logger.info(f"用户认证成功: {email}")
    return (True, user, "success")


def authenticate_user_or_raise(db: Session, email: str, password: str) -> User:
    """
    认证用户，失败时抛出异常（推荐使用）
    
    :param db: 数据库会话
    :param email: 用户邮箱
    :param password: 用户密码
    :return: 用户对象
    :raises BusinessException: 认证失败时抛出
    """
    from app.core.exceptions import BusinessException
    from app.core.error_codes import BizCode
    from app.core.logging_config import get_auth_logger
    
    logger = get_auth_logger()
    
    # 查找用户
    user = user_repository.get_user_by_email(db, email=email)
    if not user:
        logger.warning(f"用户不存在: {email}")
        raise BusinessException("用户不存在", code=BizCode.USER_NOT_FOUND)
    
    # 检查用户状态
    if not user.is_active:
        logger.warning(f"用户未激活: {email}")
        raise BusinessException("用户未激活", code=BizCode.USER_NOT_FOUND)
    
    # 验证密码
    if not verify_password(password, user.hashed_password):
        logger.warning(f"密码错误: {email}")
        raise BusinessException("密码错误", code=BizCode.PASSWORD_ERROR)
    
    logger.info(f"用户认证成功: {email}")
    return user


def get_user_by_username(db: Session, username: str) -> Optional[User]:
    """
    Get a user by username.

    :param db: The database session.
    :param username: The username.
    :return: The user object if found, otherwise None.
    """
    return user_repository.get_user_by_username(db, username=username)

def get_user_by_id(db: Session, user_id: str) -> Optional[User]:
    """
    Get a user by user_id.

    :param db: The database session.
    :param user_id: The user id (UUID string).
    :return: The user object if found, otherwise None.
    """
    return user_repository.get_user_by_id(db, user_id=user_id)


def register_user_with_invite(
    db: Session,
    email: str,
    password: str,
    invite_token: str,
    workspace_id: str
) -> User:
    """
    使用邀请码注册新用户并加入工作空间
    
    :param db: 数据库会话
    :param email: 用户邮箱
    :param password: 用户密码
    :param invite_token: 邀请令牌
    :param workspace_id: 工作空间ID
    :return: 创建的用户对象
    """
    from app.schemas.user_schema import UserCreate
    from app.schemas.workspace_schema import InviteAcceptRequest
    from app.services import user_service, workspace_service
    from app.core.logging_config import get_business_logger
    
    logger = get_business_logger()
    logger.info(f"使用邀请码注册用户: {email}")
    
    try:
        # 创建用户
        user_create = UserCreate(
            email=email,
            password=password,
            username=email.split('@')[0]
        )
        user = user_service.create_user(db=db, user=user_create)
        logger.info(f"用户创建成功: {user.email} (ID: {user.id})")
        
        # 接受工作空间邀请（此时用户已成为工作空间成员，并且会 commit）
        invite_accept = InviteAcceptRequest(token=invite_token)
        workspace_service.accept_workspace_invite(db, invite_accept, user)
        logger.info("用户接受邀请成功")
        
        # 重新查询用户对象以确保获取最新状态
        from app.repositories import user_repository
        user = user_repository.get_user_by_id(db, str(user.id))
        
        # 设置当前工作空间
        user.current_workspace_id = workspace_id
        db.commit()
        db.refresh(user)
        
        logger.info(f"用户注册并加入工作空间成功: {user.email}, workspace_id: {user.current_workspace_id}")
        return user
        
    except Exception as e:
        db.rollback()
        logger.error(f"注册用户失败: {email} - {str(e)}")
        raise

def bind_workspace_with_invite(
    db: Session,
    user: User,
    invite_token: str,
    workspace_id: str
) -> User:

    from app.schemas.user_schema import UserCreate
    from app.schemas.workspace_schema import InviteAcceptRequest
    from app.services import user_service, workspace_service
    from app.core.logging_config import get_business_logger
    
    logger = get_business_logger()
    
    try:        
        
        # 接受工作空间邀请（此时用户已成为工作空间成员，并且会 commit）
        invite_accept = InviteAcceptRequest(token=invite_token)
        workspace_service.accept_workspace_invite(db, invite_accept, user)
        logger.info("用户接受邀请成功")
        
        # 重新查询用户对象以确保获取最新状态
        from app.repositories import user_repository
        user = user_repository.get_user_by_id(db, str(user.id))
        
        # 设置当前工作空间
        user.current_workspace_id = workspace_id
        db.commit()
        db.refresh(user)
        return user
        
    except Exception as e:
        db.rollback()
        logger.error(f"绑定工作空间失败: user={user.email} - {str(e)}")
        raise


def create_access_token(user_id: str, share_token: str) -> str:
    """创建访问 token
    
    Token 不设置过期时间，只要 share_token 有效，token 就有效
    
    Args:
        user_id: 用户 ID
        share_token: 分享 token
        
    Returns:
        JWT token
    """
    payload = {
        "user_id": user_id,
        "share_token": share_token,
        "iat": int(time.time())  # 签发时间
    }
    
    token = jwt.encode(payload, TOKEN_SECRET_KEY, algorithm=TOKEN_ALGORITHM)
    return token


def decode_access_token(token: str) -> dict:
    """解码访问 token
    
    Args:
        token: JWT token
        
    Returns:
        包含 user_id 和 share_token 的字典
        
    Raises:
        BusinessException: token 无效
    """
    try:
        payload = jwt.decode(token, TOKEN_SECRET_KEY, algorithms=[TOKEN_ALGORITHM])
        return {
            "user_id": payload["user_id"],
            "share_token": payload["share_token"]
        }
    except jwt.InvalidTokenError:
        raise BusinessException("无效的访问 token", BizCode.INVALID_TOKEN)