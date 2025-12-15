import uuid
from functools import wraps

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from jose import jwt, JWTError

from app.db import get_db, SessionLocal
from app.schemas import token_schema
from app.core.config import settings
from app.core.security import get_token_id
from app.repositories import user_repository, tenant_repository
from app.repositories import workspace_repository
from app.models.user_model import User
from app.models.tenant_model import Tenants
from app.models.workspace_model import Workspace
from app.services.session_service import SessionService
from app.core.logging_config import get_auth_logger, get_security_logger
from app.core.uow import SqlAlchemyUnitOfWork, IUnitOfWork
from app.core.exceptions import PermissionDeniedException

# 获取专用日志器
auth_logger = get_auth_logger()
security_logger = get_security_logger()

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")


async def get_current_user(
        token: str = Depends(oauth2_scheme),
        db: Session = Depends(get_db)
) -> User:
    """
    获取当前认证用户
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        auth_logger.debug("开始解析JWT token")
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id: str = payload.get("sub")

        if user_id is None:
            auth_logger.warning("JWT token中缺少用户ID")
            raise credentials_exception

        token_data = token_schema.TokenData(userId=user_id)
        auth_logger.debug(f"JWT解析成功，用户ID: {user_id}")

    except JWTError as e:
        auth_logger.warning(f"JWT解析失败: {str(e)}")
        raise credentials_exception

    # 检查单点登录黑名单和用户token失效
    try:
        auth_logger.debug("检查单点登录黑名单")
        token_id = get_token_id(token)
        session_service = SessionService()

        if await session_service.is_token_blacklisted(token_id):
            auth_logger.warning(f"Token已被列入黑名单: {token_id}")
            raise credentials_exception

        # 检查用户是否重置了密码（所有旧token失效）
        invalidation_time_str = await session_service.get_user_token_invalidation_time(user_id)
        if invalidation_time_str:
            from datetime import datetime, timezone
            invalidation_time = datetime.fromisoformat(invalidation_time_str)
            token_issued_at = datetime.fromtimestamp(payload.get("iat", 0), tz=timezone.utc) if payload.get(
                "iat") else None

            if token_issued_at and token_issued_at < invalidation_time:
                auth_logger.warning(f"Token在密码重置前签发，已失效: user_id={user_id}")
                raise credentials_exception

        auth_logger.debug("单点登录检查通过")

    except HTTPException:
        raise
    except Exception as e:
        auth_logger.error(f"检查token有效性时发生错误: {str(e)}")
        raise credentials_exception

    try:
        auth_logger.debug(f"查询用户信息: {token_data.userId}")
        user = user_repository.get_user_by_id(db, user_id=token_data.userId)

        if user is None:
            auth_logger.warning(f"用户不存在: {token_data.userId}")
            raise credentials_exception
        if not user.is_active:
            auth_logger.warning(f"用户已被停用: {user.username} (ID: {user.id})")
            raise credentials_exception

        auth_logger.info(f"用户认证成功: {user.username} (ID: {user.id})")
        return user

    except Exception as e:
        auth_logger.error(f"查询用户信息时发生错误: {str(e)}")
        raise credentials_exception


async def get_current_tenant(
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db)
) -> Tenants:
    """
    获取当前用户的租户
    由于每个用户只属于一个租户，直接返回用户的租户
    """
    auth_logger.debug(f"获取用户 {current_user.username} 的租户信息")

    try:
        # 直接从用户模型获取租户
        if current_user.tenant:
            auth_logger.info(f"用户 {current_user.username} 的租户: {current_user.tenant.name}")
            return current_user.tenant
        else:
            auth_logger.warning(f"用户 {current_user.username} 没有关联的租户")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="用户没有关联的租户"
            )

    except HTTPException:
        raise
    except Exception as e:
        auth_logger.error(f"获取租户信息时发生错误: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="获取租户信息失败"
        )


async def get_user_tenants(
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db)
) -> list[Tenants]:
    """
    获取当前用户所属的所有租户
    由于每个用户只属于一个租户，返回包含该租户的列表
    """
    auth_logger.debug(f"获取用户 {current_user.username} 的所有租户")

    try:
        if current_user.tenant:
            tenants = [current_user.tenant]
            auth_logger.info(f"用户 {current_user.username} 属于 1 个租户")
            return tenants
        else:
            auth_logger.info(f"用户 {current_user.username} 没有关联的租户")
            return []

    except Exception as e:
        auth_logger.error(f"获取用户租户列表时发生错误: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="获取租户列表失败"
        )


async def get_current_superuser(
        current_user: User = Depends(get_current_user)
) -> User:
    """
    检查当前用户是否为超级管理员
    """
    auth_logger.debug(f"检查用户 {current_user.username} 是否为超级管理员")

    if not current_user.is_superuser:
        auth_logger.warning(f"用户 {current_user.username} 尝试访问超管功能但不是超级管理员")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="只有超级管理员才能执行此操作"
        )

    auth_logger.info(f"超级管理员 {current_user.username} 访问超管功能")
    return current_user


# ----------------------
# Workspace Access Guard
# ----------------------

# async def require_workspace_access(
#     workspace_id: uuid.UUID,
#     db: Session = Depends(get_db),
#     current_user: User = Depends(get_current_user),
# ) -> Workspace:
#     """
#     校验当前用户对指定工作空间的访问权限：
#     - 工作空间必须存在
#     - 超级管理员且与工作空间同租户可访问
#     - 普通用户必须是该工作空间成员

#     返回工作空间对象以便后续使用；无权限时抛出 HTTPException。
#     """
#     auth_logger.debug(f"校验工作空间访问权限: workspace_id={workspace_id}, user={current_user.id}")

#     # 1) 工作空间存在性
#     workspace = workspace_repository.get_workspace_by_id(db=db, workspace_id=workspace_id)
#     if not workspace:
#         auth_logger.warning(f"工作空间不存在: {workspace_id}")
#         raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")

#     # 2) 超级管理员（同租户）直接放行
#     if current_user.is_superuser:
#         if workspace.tenant_id == current_user.tenant_id:
#             auth_logger.debug(f"超管同租户访问放行: user={current_user.id}, workspace={workspace_id}")
#             return workspace
#         # 超管跨租户访问不允许
#         auth_logger.warning(
#             f"超管跨租户访问被拒: user_tenant={current_user.tenant_id}, workspace_tenant={workspace.tenant_id}"
#         )
#         raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

#     # 3) 普通用户需要是成员
#     member = workspace_repository.get_member_in_workspace(
#         db=db, user_id=current_user.id, workspace_id=workspace_id
#     )
#     if not member:
#         auth_logger.warning(f"非成员访问被拒: user={current_user.id}, workspace={workspace_id}")
#         raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

#     auth_logger.debug(f"成员访问通过: user={current_user.id}, workspace={workspace_id}")
#     return workspace


# # 针对创建应用的请求体（包含 workspace_id）提供便捷校验
# from app.schemas.app_schema import AppCreate

# async def require_workspace_access_for_app_create(
#     payload: AppCreate,
#     db: Session = Depends(get_db),
#     current_user: User = Depends(get_current_user),
# ) -> Workspace:
#     return await require_workspace_access(payload.workspace_id, db, current_user)


# ----------------------
# Decorator (@) version
# ----------------------

def _check_workspace_access_sync(db: Session, user: User, workspace_id: uuid.UUID) -> Workspace:
    """同步校验版本，供装饰器在同步端点中调用 - 使用权限服务"""
    auth_logger.debug(f"同步校验工作空间访问权限: workspace_id={workspace_id}, user={user.id}")

    # 1) 工作空间存在性
    workspace = workspace_repository.get_workspace_by_id(db=db, workspace_id=workspace_id)
    if not workspace:
        auth_logger.warning(f"工作空间不存在: {workspace_id}")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")

    # 2) 超级用户跳过成员检查，直接验证租户
    if user.is_superuser:
        if user.tenant_id == workspace.tenant_id:
            auth_logger.debug(f"超级用户访问同租户工作空间: workspace_id={workspace_id}, user={user.id}")
            return workspace
        else:
            auth_logger.warning(f"超级用户尝试访问其他租户工作空间: workspace_id={workspace_id}, user={user.id}")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    # 3) 普通用户使用权限服务检查访问权限
    from app.core.permissions import permission_service, Subject, Resource, Action
    from app.core.permissions.policies import WorkspaceMemberPolicy, SameTenantSuperuserPolicy

    # Check if user is a member
    member = workspace_repository.get_member_in_workspace(
        db=db, user_id=user.id, workspace_id=workspace_id
    )
    workspace_memberships = {workspace_id} if member else set()

    subject = Subject.from_user(user, workspace_memberships=workspace_memberships)
    resource = Resource.from_workspace(workspace)

    # Add workspace member policy
    temp_service = permission_service
    if member:
        temp_service.add_policy(WorkspaceMemberPolicy(allowed_actions={Action.READ, Action.UPDATE, Action.MANAGE}))
    temp_service.add_policy(SameTenantSuperuserPolicy())

    try:
        permission_service.require_permission(
            subject,
            Action.READ,
            resource,
            error_message="Forbidden"
        )
        return workspace
    except PermissionDeniedException:
        auth_logger.warning(f"工作空间访问被拒绝: workspace_id={workspace_id}, user={user.id}")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


def workspace_access_guard(get_workspace_id_from_body: bool = False):
    """
    @ 装饰器：在端点进入前执行工作空间访问校验。
    要求端点函数签名包含：
      - db: Session = Depends(get_db)
      - user 或 current_user: User = Depends(get_current_user)
      - workspace_id: uuid.UUID （query/path 参数）或 payload: AppCreate（body，含 workspace_id）
    
    支持同步和异步函数。
    """
    import asyncio

    def _decorator(func):
        # 检查函数是否是异步的
        if asyncio.iscoroutinefunction(func):
            @wraps(func)
            async def _async_wrapper(*args, **kwargs):
                db: Session = kwargs.get("db")
                user: User = kwargs.get("user") or kwargs.get("current_user")

                if get_workspace_id_from_body:
                    payload = kwargs.get("payload")
                    if not payload or not hasattr(payload, "workspace_id"):
                        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                            detail="workspace_id missing in body")
                    workspace_id = payload.workspace_id
                else:
                    workspace_id = kwargs.get("workspace_id")
                    if workspace_id is None:
                        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="workspace_id is required")

                _check_workspace_access_sync(db, user, workspace_id)
                return await func(*args, **kwargs)

            return _async_wrapper
        else:
            @wraps(func)
            def _sync_wrapper(*args, **kwargs):
                db: Session = kwargs.get("db")
                user: User = kwargs.get("user") or kwargs.get("current_user")

                if get_workspace_id_from_body:
                    payload = kwargs.get("payload")
                    if not payload or not hasattr(payload, "workspace_id"):
                        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                            detail="workspace_id missing in body")
                    workspace_id = payload.workspace_id
                else:
                    workspace_id = kwargs.get("workspace_id")
                    if workspace_id is None:
                        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="workspace_id is required")

                _check_workspace_access_sync(db, user, workspace_id)
                return func(*args, **kwargs)

            return _sync_wrapper

    return _decorator


def get_uow() -> IUnitOfWork:
    """
    获取工作单元实例
    
    Returns:
        IUnitOfWork: 工作单元实例
    """
    return SqlAlchemyUnitOfWork(SessionLocal)


def cur_workspace_access_guard():
    """
    @ 装饰器：在端点进入前执行工作空间访问校验。
    要求端点函数签名包含：
      - db: Session = Depends(get_db)
      - current_user: User = Depends(get_current_user)
    
    支持同步和异步函数。
    """
    import asyncio
    import inspect

    def _decorator(func):
        # 检查函数是否是异步的
        if asyncio.iscoroutinefunction(func):
            @wraps(func)
            async def _async_wrapper(*args, **kwargs):
                db: Session = kwargs.get("db")
                user: User = kwargs.get("current_user")
                workspace_id = user.current_workspace_id
                if workspace_id is None:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="workspace_id is required")
                _check_workspace_access_sync(db, user, workspace_id)
                return await func(*args, **kwargs)

            return _async_wrapper
        else:
            @wraps(func)
            def _sync_wrapper(*args, **kwargs):
                db: Session = kwargs.get("db")
                user: User = kwargs.get("current_user")
                workspace_id = user.current_workspace_id
                if workspace_id is None:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="workspace_id is required")
                _check_workspace_access_sync(db, user, workspace_id)
                return func(*args, **kwargs)

            return _sync_wrapper

    return _decorator


class ShareTokenData:
    """分享 token 数据"""

    def __init__(self, user_id: str, share_token: str):
        self.user_id = user_id
        self.share_token = share_token


async def get_share_user_id(
        token: str = Depends(oauth2_scheme),
        db: Session = Depends(get_db)
) -> ShareTokenData:
    """
    从分享访问 token 中获取用户 ID 和 share_token
    
    这个函数用于公开分享的接口，验证访问 token 并返回用户信息
    不需要验证用户是否存在或激活，只需要验证 token 的有效性和 share_token 是否有效
    
    Returns:
        ShareTokenData: 包含 user_id 和 share_token
    """
    from app.services.auth_service import decode_access_token
    from app.services.release_share_service import ReleaseShareService
    from app.core.exceptions import BusinessException

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        auth_logger.debug("开始解析分享访问 token")

        # 解码 token 获取 user_id 和 share_token
        payload = decode_access_token(token)
        user_id = payload["user_id"]
        share_token = payload["share_token"]

        auth_logger.debug(f"Token 解析成功，用户ID: {user_id}, share_token: {share_token}")

        # 验证 share_token 是否有效
        service = ReleaseShareService(db)
        share_info = service.get_shared_release_info(share_token=share_token)

        if not share_info:
            auth_logger.warning(f"分享 token 无效: {share_token}")
            raise credentials_exception

        auth_logger.info(f"分享访问验证成功: user_id={user_id}, share_token={share_token}")
        return ShareTokenData(user_id=user_id, share_token=share_token)

    except BusinessException as e:
        auth_logger.warning(f"分享访问验证失败: {str(e)}")
        raise credentials_exception
    except Exception as e:
        auth_logger.error(f"验证分享访问 token 时发生错误: {str(e)}")
        raise credentials_exception



