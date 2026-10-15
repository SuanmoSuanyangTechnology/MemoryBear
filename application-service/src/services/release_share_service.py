"""Release-share service (frozen port of the host ``ReleaseShareService``).

Only the management face is ported: create-or-update, update, read, delete and
token regeneration of a release's share configuration. The public share-view
resolution (password check, embed code, view counting) belongs to the runtime
plane.
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.orm import Session

from ..errors import BizCode, BusinessException, ResourceNotFoundException
from ..models.app_model import App
from ..models.app_release_model import AppRelease
from ..models.release_share_model import ReleaseShare
from ..repositories.release_share_repository import ReleaseShareRepository
from ..schemas import release_share_schema
from ..utils.share_utils import build_share_url, generate_share_token, hash_password

logger = logging.getLogger(__name__)


class ReleaseShareService:
    """Release share service."""

    def __init__(self, db: Session):
        self.db = db
        self.repo = ReleaseShareRepository(db)

    def create_or_update_share(
        self,
        release_id: UUID,
        user_id: UUID,
        workspace_id: UUID,
        data: release_share_schema.ReleaseShareCreate | release_share_schema.ReleaseShareUpdate,
    ) -> ReleaseShare:
        """Create the share configuration, or overwrite the existing one."""

        release = self._get_release_or_404(release_id)
        self._validate_release_access(release, workspace_id)

        existing_share = self.repo.get_by_release_id(release_id)
        if existing_share:
            return self._update_share_internal(existing_share, data)
        return self._create_share_internal(release, user_id, data)

    def update_share(
        self,
        release_id: UUID,
        workspace_id: UUID,
        data: release_share_schema.ReleaseShareUpdate,
    ) -> ReleaseShare:
        """Update the share configuration of one release."""

        release = self._get_release_or_404(release_id)
        self._validate_release_access(release, workspace_id)

        share = self.repo.get_by_release_id(release_id)
        if not share:
            raise ResourceNotFoundException("分享配置", str(release_id))

        return self._update_share_internal(share, data)

    def get_share(
        self,
        release_id: UUID,
        workspace_id: UUID,
        base_url: str | None = None,
    ) -> release_share_schema.ReleaseShare | None:
        """Read the share configuration; None when none exists."""

        release = self._get_release_or_404(release_id)
        self._validate_release_access(release, workspace_id)

        share = self.repo.get_by_release_id(release_id)
        if not share:
            return None

        return self.convert_to_schema(share, base_url)

    def delete_share(self, release_id: UUID, workspace_id: UUID) -> None:
        """Delete the share configuration (its public link stops resolving)."""

        release = self._get_release_or_404(release_id)
        self._validate_release_access(release, workspace_id)

        share = self.repo.get_by_release_id(release_id)
        if not share:
            raise ResourceNotFoundException("分享配置", str(release_id))

        self.repo.delete(share)

        logger.info(
            "Release share deleted: share_id=%s, release_id=%s", share.id, release_id
        )

    def regenerate_token(self, release_id: UUID, workspace_id: UUID) -> ReleaseShare:
        """Mint a fresh share token, invalidating the previous link."""

        release = self._get_release_or_404(release_id)
        self._validate_release_access(release, workspace_id)

        share = self.repo.get_by_release_id(release_id)
        if not share:
            raise ResourceNotFoundException("分享配置", str(release_id))

        old_token = share.share_token
        share.share_token = self._generate_unique_token()
        share = self.repo.update(share)

        logger.info(
            "Release share token regenerated: share_id=%s, old_token=%s, new_token=%s",
            share.id,
            old_token,
            share.share_token,
        )

        return share

    def convert_to_schema(
        self,
        share: ReleaseShare,
        base_url: str | None = None,
    ) -> release_share_schema.ReleaseShare:
        """Convert a share row to its output schema, rebuilding the public URL."""

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
            updated_at=share.updated_at,
        )

    def _create_share_internal(
        self,
        release: AppRelease,
        user_id: UUID,
        data: release_share_schema.ReleaseShareCreate | release_share_schema.ReleaseShareUpdate,
    ) -> ReleaseShare:
        """Create a share configuration with a unique token."""

        share_token = self._generate_unique_token()

        password_hash = None
        if data.require_password and data.password:
            password_hash = hash_password(data.password)

        share = ReleaseShare(
            release_id=release.id,
            app_id=release.app_id,
            is_enabled=data.is_enabled,
            share_token=share_token,
            require_password=data.require_password,
            password_hash=password_hash,
            allow_embed=data.allow_embed,
            embed_domains=data.embed_domains or [],
            created_by=user_id,
        )

        share = self.repo.create(share)

        logger.info(
            "Release share created: share_id=%s, release_id=%s, app_id=%s",
            share.id,
            release.id,
            release.app_id,
        )

        return share

    def _update_share_internal(
        self,
        share: ReleaseShare,
        data: release_share_schema.ReleaseShareCreate | release_share_schema.ReleaseShareUpdate,
    ) -> ReleaseShare:
        """Apply the provided fields onto an existing share configuration."""

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
            "Release share updated: share_id=%s, release_id=%s", share.id, share.release_id
        )

        return share

    def _generate_unique_token(self, max_attempts: int = 10) -> str:
        """Mint a token that no share row has taken yet."""

        for _ in range(max_attempts):
            token = generate_share_token()
            if not self.repo.token_exists(token):
                return token

        raise BusinessException("生成唯一 token 失败，请重试", BizCode.INTERNAL_ERROR)

    def _get_release_or_404(self, release_id: UUID) -> AppRelease:
        """Fetch one release or raise the host-shaped not-found error."""

        release = self.db.get(AppRelease, release_id)
        if not release:
            raise ResourceNotFoundException("发布版本", str(release_id))
        return release

    def _validate_release_access(self, release: AppRelease, workspace_id: UUID) -> None:
        """The release's app must live in the acting workspace."""

        app = self.db.get(App, release.app_id)
        if not app:
            raise ResourceNotFoundException("应用", str(release.app_id))

        if app.workspace_id != workspace_id:
            raise BusinessException("无权访问该发布版本", BizCode.PERMISSION_DENIED)


__all__ = ["ReleaseShareService"]
