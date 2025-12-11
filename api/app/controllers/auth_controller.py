from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.response_utils import success
from app.db import get_db
from app.schemas.response_schema import ApiResponse
from app.schemas.token_schema import Token, RefreshTokenRequest, TokenRequest
from app.schemas.workspace_schema import InviteAcceptRequest
from app.services import auth_service, user_service, workspace_service
from app.core import security
from app.core.config import settings
from app.services.session_service import SessionService
from app.core.logging_config import get_auth_logger, get_security_logger
from app.core.exceptions import BusinessException
from app.core.error_codes import BizCode
from app.dependencies import get_current_user, oauth2_scheme
from app.models.user_model import User

# 获取专用日志器
auth_logger = get_auth_logger()
security_logger = get_security_logger()

router = APIRouter(tags=["Authentication"])

@router.post("/token", response_model=ApiResponse)
async def login_for_access_token(
    form_data: TokenRequest,
    db: Session = Depends(get_db)
):
    """用户登录获取token"""
    auth_logger.info(f"用户登录请求: {form_data.email}")
    
    # 验证邀请码（如果提供）
    invite_info = None
    # 验证用户凭据或注册新用户
    user = None
    if form_data.invite:
        auth_logger.info(f"检测到邀请码: {form_data.invite[:8]}...")
        invite_info = workspace_service.validate_invite_token(db, form_data.invite)
        
        if not invite_info.is_valid:
            raise BusinessException("邀请码无效或已过期", code=BizCode.BAD_REQUEST)
        
        if invite_info.email != form_data.email:
            raise BusinessException("邀请邮箱与登录邮箱不匹配", code=BizCode.BAD_REQUEST)        
        auth_logger.info(f"邀请码验证成功: workspace={invite_info.workspace_name}")
        try:
            # 尝试认证用户
            user = auth_service.authenticate_user_or_raise(db, form_data.email, form_data.password)
            auth_logger.info(f"用户认证成功: {user.email} (ID: {user.id})")
            if form_data.invite:
                auth_service.bind_workspace_with_invite(db=db,
                user=user,        
                invite_token=form_data.invite,
                workspace_id=invite_info.workspace_id)
        except BusinessException as e:
        # 用户不存在且有邀请码，尝试注册
            if e.code == BizCode.USER_NOT_FOUND:
                auth_logger.info(f"用户不存在，使用邀请码注册: {form_data.email}")
                user = auth_service.register_user_with_invite(
                db=db,
                email=form_data.email,
                password=form_data.password,
                invite_token=form_data.invite,
                workspace_id=invite_info.workspace_id
            )
            elif e.code == BizCode.PASSWORD_ERROR:
                # 用户存在但密码错误
                auth_logger.warning(f"接受邀请失败，密码验证错误: {form_data.email}")
                raise BusinessException("接受邀请失败，密码验证错误", BizCode.LOGIN_FAILED)
            else:
                # 其他认证失败情况，直接抛出
                raise
    else:
        try:
        # 尝试认证用户
            user = auth_service.authenticate_user_or_raise(db, form_data.email, form_data.password)
            auth_logger.info(f"用户认证成功: {user.email} (ID: {user.id})")

        except BusinessException as e:
            
            # 其他认证失败情况，直接抛出
            raise BusinessException(e.message,BizCode.LOGIN_FAILED)

    # 创建 tokens
    access_token, access_token_id = security.create_access_token(subject=user.id)
    refresh_token, refresh_token_id = security.create_refresh_token(subject=user.id)
    
    # 计算过期时间
    access_expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    refresh_expires_at = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    
    # 单点登录会话管理
    if settings.ENABLE_SINGLE_SESSION:
        await SessionService.invalidate_old_session(user.id, access_token_id)
        await SessionService.set_user_active_session(user.id, access_token_id, access_expires_at)
    
    # 更新最后登录时间
    user_service.update_last_login_time(db, user.id)
    
    auth_logger.info(f"用户 {user.username} 登录成功")
    
    return success(
        data=Token(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",
            expires_at=access_expires_at,
            refresh_expires_at=refresh_expires_at
        ),
        msg="登录成功"
    )


@router.post("/refresh", response_model=ApiResponse)
async def refresh_token(
    refresh_request: RefreshTokenRequest,
    db: Session = Depends(get_db)
):
    """刷新token"""
    auth_logger.info("收到token刷新请求")
    
    # 验证 refresh token
    userId = security.verify_token(refresh_request.refresh_token, "refresh")
    if not userId:
        raise BusinessException("无效的refresh token", code=BizCode.TOKEN_INVALID)
    
    # 检查用户是否存在
    user = auth_service.get_user_by_id(db, userId)
    if not user:
        raise BusinessException("用户不存在", code=BizCode.USER_NOT_FOUND)
    
    # 检查 refresh token 黑名单
    if settings.ENABLE_SINGLE_SESSION:
        refresh_token_id = security.get_token_id(refresh_request.refresh_token)
        if refresh_token_id and await SessionService.is_token_blacklisted(refresh_token_id):
            raise BusinessException("Refresh token已失效", code=BizCode.TOKEN_BLACKLISTED)
    
    # 生成新 tokens
    new_access_token, new_access_token_id = security.create_access_token(subject=user.id)
    new_refresh_token, new_refresh_token_id = security.create_refresh_token(subject=user.id)
    
    # 计算过期时间
    access_expires_at = datetime.now() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    refresh_expires_at = datetime.now() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    
    # 单点登录会话管理
    if settings.ENABLE_SINGLE_SESSION:
        # 将旧 refresh token 加入黑名单
        old_refresh_token_id = security.get_token_id(refresh_request.refresh_token)
        if old_refresh_token_id:
            await SessionService.blacklist_token(old_refresh_token_id)
        
        # 更新会话
        await SessionService.invalidate_old_session(user.id, new_access_token_id)
        await SessionService.set_user_active_session(user.id, new_access_token_id, access_expires_at)
    
    auth_logger.info(f"用户 {user.id} token刷新成功")
    
    return success(
        data=Token(
            access_token=new_access_token,
            refresh_token=new_refresh_token,
            token_type="bearer",
            expires_at=access_expires_at,
            refresh_expires_at=refresh_expires_at
        ),
        msg="token刷新成功"
    )


@router.post("/logout", response_model=ApiResponse)
async def logout(
    token: str = Depends(oauth2_scheme),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """登出当前用户：加入token黑名单并清理会话"""
    auth_logger.info(f"用户 {current_user.username} 请求登出")
    
    token_id = security.get_token_id(token)
    if not token_id:
        raise BusinessException("无效的access token", code=BizCode.TOKEN_INVALID)

    # 加入黑名单
    await SessionService.blacklist_token(token_id)

    # 清理会话
    if settings.ENABLE_SINGLE_SESSION:
        await SessionService.clear_user_session(current_user.username)

    auth_logger.info(f"用户 {current_user.username} 登出成功")
    return success(msg="登出成功")

