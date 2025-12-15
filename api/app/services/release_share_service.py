import uuid
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models import ReleaseShare, AppRelease, App, AgentConfig
from app.repositories.release_share_repository import ReleaseShareRepository
from app.core.share_utils import (
    generate_share_token, 
    hash_password, 
    verify_password,
    build_share_url,
    generate_embed_code
)
from app.core.exceptions import ResourceNotFoundException, BusinessException
from app.core.error_codes import BizCode
from app.core.logging_config import get_business_logger
from app.schemas import release_share_schema

logger = get_business_logger()


class ReleaseShareService:
    """发布版本分享服务"""
    
    def __init__(self, db: Session):
        self.db = db
        self.repo = ReleaseShareRepository(db)
    
    def create_or_update_share(
        self,
        release_id: uuid.UUID,
        user_id: uuid.UUID,
        workspace_id: uuid.UUID,
        data: release_share_schema.ReleaseShareCreate,
        base_url: Optional[str] = None
    ) -> ReleaseShare:
        """创建或更新分享配置
        
        Args:
            release_id: 发布版本 ID
            user_id: 用户 ID
            workspace_id: 工作空间 ID
            data: 分享配置数据
            base_url: 基础 URL（用于生成完整的分享链接）
            
        Returns:
            分享配置
        """
        # 验证发布版本存在且属于该工作空间
        release = self._get_release_or_404(release_id)
        self._validate_release_access(release, workspace_id)
        
        # 检查是否已存在分享配置
        existing_share = self.repo.get_by_release_id(release_id)
        
        if existing_share:
            # 更新现有配置
            return self._update_share_internal(existing_share, data)
        else:
            # 创建新配置
            return self._create_share_internal(release, user_id, data)
    
    def _create_share_internal(
        self,
        release: AppRelease,
        user_id: uuid.UUID,
        data: release_share_schema.ReleaseShareCreate
    ) -> ReleaseShare:
        """内部方法：创建分享配置"""
        # 生成唯一的 share_token
        share_token = self._generate_unique_token()
        
        # 处理密码
        password_hash = None
        if data.require_password and data.password:
            password_hash = hash_password(data.password)
        
        # 创建分享配置
        share = ReleaseShare(
            release_id=release.id,
            app_id=release.app_id,
            is_enabled=data.is_enabled,
            share_token=share_token,
            require_password=data.require_password,
            password_hash=password_hash,
            allow_embed=data.allow_embed,
            embed_domains=data.embed_domains or [],
            created_by=user_id
        )
        
        share = self.repo.create(share)
        
        logger.info(
            "创建分享配置",
            extra={
                "share_id": str(share.id),
                "release_id": str(release.id),
                "app_id": str(release.app_id),
                "share_token": share_token
            }
        )
        
        return share
    
    def _update_share_internal(
        self,
        share: ReleaseShare,
        data: release_share_schema.ReleaseShareUpdate
    ) -> ReleaseShare:
        """内部方法：更新分享配置"""
        if data.is_enabled is not None:
            share.is_enabled = data.is_enabled
        
        if data.require_password is not None:
            share.require_password = data.require_password
            
        if data.password is not None:
            if data.password:
                share.password_hash = hash_password(data.password)
            else:
                share.password_hash = None
        
        if data.allow_embed is not None:
            share.allow_embed = data.allow_embed
        
        if data.embed_domains is not None:
            share.embed_domains = data.embed_domains or []
        
        share = self.repo.update(share)
        
        logger.info(
            "更新分享配置",
            extra={
                "share_id": str(share.id),
                "release_id": str(share.release_id)
            }
        )
        
        return share
    
    def update_share(
        self,
        release_id: uuid.UUID,
        workspace_id: uuid.UUID,
        data: release_share_schema.ReleaseShareUpdate
    ) -> ReleaseShare:
        """更新分享配置
        
        Args:
            release_id: 发布版本 ID
            workspace_id: 工作空间 ID
            data: 更新数据
            
        Returns:
            更新后的分享配置
        """
        # 验证发布版本
        release = self._get_release_or_404(release_id)
        self._validate_release_access(release, workspace_id)
        
        # 获取分享配置
        share = self.repo.get_by_release_id(release_id)
        if not share:
            raise ResourceNotFoundException("分享配置", str(release_id))
        
        return self._update_share_internal(share, data)
    
    def get_share(
        self,
        release_id: uuid.UUID,
        workspace_id: uuid.UUID,
        base_url: Optional[str] = None
    ) -> Optional[release_share_schema.ReleaseShare]:
        """获取分享配置
        
        Args:
            release_id: 发布版本 ID
            workspace_id: 工作空间 ID
            base_url: 基础 URL
            
        Returns:
            分享配置 Schema
        """
        # 验证发布版本
        release = self._get_release_or_404(release_id)
        self._validate_release_access(release, workspace_id)
        
        share = self.repo.get_by_release_id(release_id)
        if not share:
            return None
        
        return self._convert_to_schema(share, base_url)
    
    def delete_share(
        self,
        release_id: uuid.UUID,
        workspace_id: uuid.UUID
    ) -> None:
        """删除（禁用）分享配置
        
        Args:
            release_id: 发布版本 ID
            workspace_id: 工作空间 ID
        """
        # 验证发布版本
        release = self._get_release_or_404(release_id)
        self._validate_release_access(release, workspace_id)
        
        share = self.repo.get_by_release_id(release_id)
        if not share:
            raise ResourceNotFoundException("分享配置", str(release_id))
        
        self.repo.delete(share)
        
        logger.info(
            "删除分享配置",
            extra={
                "share_id": str(share.id),
                "release_id": str(release_id)
            }
        )
    
    def regenerate_token(
        self,
        release_id: uuid.UUID,
        workspace_id: uuid.UUID
    ) -> ReleaseShare:
        """重新生成分享 token（旧链接失效）
        
        Args:
            release_id: 发布版本 ID
            workspace_id: 工作空间 ID
            
        Returns:
            更新后的分享配置
        """
        # 验证发布版本
        release = self._get_release_or_404(release_id)
        self._validate_release_access(release, workspace_id)
        
        share = self.repo.get_by_release_id(release_id)
        if not share:
            raise ResourceNotFoundException("分享配置", str(release_id))
        
        # 生成新 token
        old_token = share.share_token
        share.share_token = self._generate_unique_token()
        share = self.repo.update(share)
        
        logger.info(
            "重新生成分享 token",
            extra={
                "share_id": str(share.id),
                "old_token": old_token,
                "new_token": share.share_token
            }
        )
        
        return share
    
    def get_shared_release_info(
        self,
        share_token: str,
        password: Optional[str] = None
    ) -> release_share_schema.SharedReleaseInfo:
        """获取公开分享的发布版本信息
        
        Args:
            share_token: 分享 token
            password: 访问密码（如果需要）
            
        Returns:
            分享的发布版本信息
        """
        # 获取分享配置
        share = self.repo.get_by_share_token(share_token)
        if not share:
            raise ResourceNotFoundException("分享链接", share_token)
        
        # 检查是否启用
        if not share.is_enabled:
            raise BusinessException("该分享链接已禁用", BizCode.SHARE_DISABLED)
        
        # 验证密码
        is_password_verified = False
        if share.require_password:
            if not password:
                # 需要密码但未提供，返回基本信息
                release = self.db.get(AppRelease, share.release_id)
                return release_share_schema.SharedReleaseInfo(
                    app_name=release.name,
                    app_description=release.description,
                    app_icon=release.icon,
                    app_type=release.type,
                    version=release.version,
                    release_notes=release.release_notes,
                    published_at=int(release.published_at.timestamp() * 1000),
                    config={},
                    require_password=True,
                    is_password_verified=False,
                    allow_embed=share.allow_embed
                )
            
            # 验证密码
            if not share.password_hash or not verify_password(password, share.password_hash):
                raise BusinessException("密码错误", BizCode.INVALID_PASSWORD)
            
            is_password_verified = True
        
        # 获取发布版本详细信息
        release = self.db.get(AppRelease, share.release_id)
        if not release:
            raise ResourceNotFoundException("发布版本", str(share.release_id))
        
        # 异步更新访问统计（不阻塞响应）
        try:
            self.repo.increment_view_count(share.id)
        except Exception as e:
            logger.warning(f"更新访问统计失败: {str(e)}")
        
        # 返回完整信息
        return release_share_schema.SharedReleaseInfo(
            app_name=release.name,
            app_description=release.description,
            app_icon=release.icon,
            app_type=release.type,
            version=release.version,
            release_notes=release.release_notes,
            published_at=int(release.published_at.timestamp() * 1000),
            config=release.config or {},
            require_password=share.require_password,
            is_password_verified=is_password_verified,
            allow_embed=share.allow_embed
        )
    
    def verify_password(
        self,
        share_token: str,
        password: str
    ) -> bool:
        """验证分享密码
        
        Args:
            share_token: 分享 token
            password: 密码
            
        Returns:
            是否验证成功
        """
        share = self.repo.get_by_share_token(share_token)
        if not share:
            raise ResourceNotFoundException("分享链接", share_token)
        
        if not share.is_enabled:
            raise BusinessException("该分享链接已禁用", BizCode.SHARE_DISABLED)
        
        if not share.require_password:
            return True
        
        if not share.password_hash:
            return False
        
        return verify_password(password, share.password_hash)
    
    def get_embed_code(
        self,
        share_token: str,
        width: str = "100%",
        height: str = "600px",
        base_url: Optional[str] = None
    ) -> release_share_schema.EmbedCode:
        """获取嵌入代码
        
        Args:
            share_token: 分享 token
            width: 宽度
            height: 高度
            base_url: 基础 URL
            
        Returns:
            嵌入代码
        """
        share = self.repo.get_by_share_token(share_token)
        if not share:
            raise ResourceNotFoundException("分享链接", share_token)
        
        if not share.is_enabled:
            raise BusinessException("该分享链接已禁用", BizCode.SHARE_DISABLED)
        
        if not share.allow_embed:
            raise BusinessException("该分享不允许嵌入", BizCode.EMBED_NOT_ALLOWED)
        
        embed_data = generate_embed_code(share_token, width, height, base_url)
        return release_share_schema.EmbedCode(**embed_data)
    
    def _generate_unique_token(self, max_attempts: int = 10) -> str:
        """生成唯一的分享 token"""
        for _ in range(max_attempts):
            token = generate_share_token()
            if not self.repo.token_exists(token):
                return token
        
        raise BusinessException("生成唯一 token 失败，请重试", BizCode.INTERNAL_ERROR)
    
    def _get_release_or_404(self, release_id: uuid.UUID) -> AppRelease:
        """获取发布版本或抛出 404"""
        release = self.db.get(AppRelease, release_id)
        if not release:
            raise ResourceNotFoundException("发布版本", str(release_id))
        return release
    
    def _validate_release_access(self, release: AppRelease, workspace_id: uuid.UUID) -> None:
        """验证发布版本访问权限"""
        app = self.db.get(App, release.app_id)
        if not app:
            raise ResourceNotFoundException("应用", str(release.app_id))
        
        if app.workspace_id != workspace_id:
            raise BusinessException("无权访问该发布版本", BizCode.PERMISSION_DENIED)
    
    def _convert_to_schema(
        self,
        share: ReleaseShare,
        base_url: Optional[str] = None
    ) -> release_share_schema.ReleaseShare:
        """转换为 Schema"""
        share_url = build_share_url(share.share_token, base_url)
        
        return release_share_schema.ReleaseShare(
            id=share.id,
            release_id=share.release_id,
            app_id=share.app_id,
            is_enabled=share.is_enabled,
            share_token=share.share_token,
            share_url=share_url,
            require_password=share.require_password,
            allow_embed=share.allow_embed,
            embed_domains=share.embed_domains or [],
            view_count=share.view_count,
            last_accessed_at=share.last_accessed_at,
            created_at=share.created_at,
            updated_at=share.updated_at
        )
