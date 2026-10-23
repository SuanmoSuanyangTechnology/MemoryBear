"""
App share service.

Provides the app-sharing business logic:
- Share an app to other workspaces
- Unshare / bulk unshare
- Update share permissions
- Query and serialize share records

Share-record serialization batches the monolith's per-row relationship lookups
(source app name/type/release/active state, target workspace name/icon).
"""
import logging
import uuid

from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.orm import Session

from ..errors import BizCode, BusinessException, ResourceNotFoundException
from ..models import App, AppRelease, AppShare
from ..models.app_model import AppType
from ..repositories import cross_domain_read
from ..schemas import app_schema
from ..utils.datetime_utils import utcnow_naive
from .app_service import AppService

logger = logging.getLogger(__name__)


class AppShareService:
    """App share service class.

    Reuses the ``AppService`` authorization helpers (fetch app, validate
    workspace access) and separately owns the share-record lifecycle.
    """

    def __init__(self, db: Session, app_service: AppService | None = None):
        self.db = db
        self.app_service = app_service or AppService(db)

    # ==================== App sharing ====================

    def share_app(
            self,
            *,
            app_id: uuid.UUID,
            target_workspace_ids: list[uuid.UUID],
            user_id: uuid.UUID,
            workspace_id: uuid.UUID | None = None,
            permission: str = "readonly"
    ) -> list[AppShare]:
        """Share an app to other workspaces.

        Args:
            app_id: App ID
            target_workspace_ids: List of target workspace IDs
            user_id: Sharing user ID
            workspace_id: Current workspace ID (used for permission validation)

        Returns:
            List[AppShare]: Created share records

        Raises:
            ResourceNotFoundException: When the app does not exist
            BusinessException: When the app is not in the given workspace or the
                target workspace is invalid
        """

        logger.info(
            "分享应用",
            extra={
                "app_id": str(app_id),
                "target_workspaces": [str(wid) for wid in target_workspace_ids],
                "user_id": str(user_id)
            }
        )

        # 1. Validate the app
        app = self.app_service._get_app_or_404(app_id)
        self.app_service._validate_workspace_access(app, workspace_id)

        # Only agent and workflow types can be shared; multi_agent is unsupported
        if app.type == AppType.MULTI_AGENT:
            raise BusinessException(
                "集群 Agent 不支持共享应用功能",
                BizCode.INVALID_PARAMETER
            )

        # 2. Validate the target workspaces
        for target_ws_id in target_workspace_ids:
            target_ws = cross_domain_read.get_workspace(self.db, target_ws_id)
            if not target_ws:
                raise ResourceNotFoundException("工作空间", str(target_ws_id))

            # Cannot share to the app's own workspace
            if target_ws_id == app.workspace_id:
                raise BusinessException(
                    "不能分享应用到自己的工作空间",
                    BizCode.INVALID_PARAMETER
                )

        # 3. Create share records
        now = utcnow_naive()
        shares = []

        for target_ws_id in target_workspace_ids:
            # Skip if already shared
            stmt = select(AppShare).where(
                AppShare.source_app_id == app_id,
                AppShare.target_workspace_id == target_ws_id,
                AppShare.is_active.is_(True)
            )
            existing_share = self.db.scalars(stmt).first()

            if existing_share:
                logger.debug(
                    "应用已分享到该工作空间，跳过",
                    extra={"app_id": str(app_id), "target_workspace_id": str(target_ws_id)}
                )
                shares.append(existing_share)
                continue

            # Create a new share record
            share = AppShare(
                id=uuid.uuid4(),
                source_app_id=app_id,
                source_workspace_id=app.workspace_id,
                target_workspace_id=target_ws_id,
                shared_by=user_id,
                permission=permission,
                created_at=now,
                updated_at=now
            )
            self.db.add(share)
            shares.append(share)

            logger.debug(
                "创建分享记录",
                extra={"app_id": str(app_id), "target_workspace_id": str(target_ws_id)}
            )

        self.db.commit()

        logger.info(
            "应用分享成功",
            extra={
                "app_id": str(app_id),
                "shared_count": len(shares),
                "app_name": app.name
            }
        )

        return shares

    def unshare_app(
            self,
            *,
            app_id: uuid.UUID,
            target_workspace_id: uuid.UUID,
            workspace_id: uuid.UUID | None = None
    ) -> None:
        """Unshare an app.

        Args:
            app_id: App ID
            target_workspace_id: Target workspace ID
            workspace_id: Current workspace ID (used for permission validation)

        Raises:
            ResourceNotFoundException: When the app or share record does not exist
            BusinessException: When the app is not in the given workspace
        """
        logger.info(
            "取消应用分享",
            extra={
                "app_id": str(app_id),
                "target_workspace_id": str(target_workspace_id)
            }
        )

        # 1. Validate the app
        app = self.app_service._get_app_or_404(app_id)
        self.app_service._validate_workspace_access(app, workspace_id)

        # 2. Look up the share record
        stmt = select(AppShare).where(
            AppShare.source_app_id == app_id,
            AppShare.target_workspace_id == target_workspace_id,
            AppShare.is_active.is_(True)
        )
        share = self.db.scalars(stmt).first()

        if not share:
            logger.warning(
                "分享记录不存在",
                extra={"app_id": str(app_id), "target_workspace_id": str(target_workspace_id)}
            )
            raise ResourceNotFoundException(
                "分享记录",
                f"app_id={app_id}, target_workspace_id={target_workspace_id}"
            )

        # 3. Soft-delete the share record
        share.is_active = False
        self.db.commit()

        logger.info(
            "应用分享已取消",
            extra={"app_id": str(app_id), "target_workspace_id": str(target_workspace_id)}
        )

    def unshare_all_apps_to_workspace(
            self,
            *,
            target_workspace_id: uuid.UUID,
            workspace_id: uuid.UUID
    ) -> int:
        """Cancel all app shares from current workspace to a target workspace.

        Args:
            target_workspace_id: Target workspace ID to cancel all shares to
            workspace_id: Current workspace ID (source)

        Returns:
            Number of share records deleted
        """
        logger.info(
            "取消对目标工作空间的所有应用分享",
            extra={
                "target_workspace_id": str(target_workspace_id),
                "workspace_id": str(workspace_id),
            }
        )

        # Query active records first for reliable count
        id_stmt = select(AppShare.id).where(
            AppShare.source_workspace_id == workspace_id,
            AppShare.target_workspace_id == target_workspace_id,
            AppShare.is_active.is_(True)
        )
        ids = list(self.db.scalars(id_stmt).all())
        count = len(ids)

        if ids:
            # Soft delete: mark as inactive
            self.db.execute(
                sa_update(AppShare).where(AppShare.id.in_(ids)).values(is_active=False)
            )
            self.db.commit()

        logger.info("已取消分享记录数", extra={"count": count})
        return count

    def list_app_shares(
            self,
            *,
            app_id: uuid.UUID,
            workspace_id: uuid.UUID | None = None
    ) -> list[AppShare]:
        """List all share records of an app.

        Args:
            app_id: App ID
            workspace_id: Current workspace ID (used for permission validation)

        Returns:
            List[AppShare]: Share records

        Raises:
            ResourceNotFoundException: When the app does not exist
            BusinessException: When the app is not in the given workspace
        """
        logger.debug("列出应用分享记录", extra={"app_id": str(app_id)})

        # Validate the app
        app = self.app_service._get_app_or_404(app_id)
        self.app_service._validate_workspace_access(app, workspace_id)

        # Query share records
        stmt = select(AppShare).where(
            AppShare.source_app_id == app_id,
            AppShare.is_active.is_(True)
        ).order_by(AppShare.created_at.desc())

        shares = list(self.db.scalars(stmt).all())

        logger.debug(
            "应用分享记录查询完成",
            extra={"app_id": str(app_id), "count": len(shares)}
        )

        return shares

    def remove_shared_app(
            self,
            *,
            app_id: uuid.UUID,
            workspace_id: uuid.UUID
    ) -> None:
        """Remove a shared app from the recipient's own workspace.

        Only the share record is deleted; the source app is unaffected.

        Args:
            app_id: App ID
            workspace_id: Current workspace ID (the target workspace of the share)

        Raises:
            ResourceNotFoundException: When the share record does not exist
        """
        logger.info(
            "移除共享应用",
            extra={"app_id": str(app_id), "workspace_id": str(workspace_id)}
        )

        stmt = select(AppShare).where(
            AppShare.source_app_id == app_id,
            AppShare.target_workspace_id == workspace_id,
            AppShare.is_active.is_(True)
        )
        share = self.db.scalars(stmt).first()

        if not share:
            raise ResourceNotFoundException(
                "共享记录",
                f"app_id={app_id}, workspace_id={workspace_id}"
            )

        # Soft delete
        share.is_active = False
        self.db.commit()

        logger.info(
            "共享应用已移除",
            extra={"app_id": str(app_id), "workspace_id": str(workspace_id)}
        )

    def remove_all_shared_apps_from_workspace(
            self,
            *,
            source_workspace_id: uuid.UUID,
            workspace_id: uuid.UUID
    ) -> int:
        """Remove all shared apps from a specific source workspace.

        Args:
            source_workspace_id: The workspace that shared the apps
            workspace_id: Current workspace ID (recipient)

        Returns:
            Number of share records deleted
        """
        logger.info(
            "批量移除来源工作空间的共享应用",
            extra={
                "source_workspace_id": str(source_workspace_id),
                "workspace_id": str(workspace_id),
            }
        )

        # Query active records for reliable count, then soft delete
        id_stmt = select(AppShare.id).where(
            AppShare.source_workspace_id == source_workspace_id,
            AppShare.target_workspace_id == workspace_id,
            AppShare.is_active.is_(True)
        )
        ids = list(self.db.scalars(id_stmt).all())
        count = len(ids)

        if ids:
            self.db.execute(
                sa_update(AppShare).where(AppShare.id.in_(ids)).values(is_active=False)
            )
            self.db.commit()

        logger.info("已移除共享记录数", extra={"count": count})
        return count

    def list_my_shared_out(
            self,
            *,
            workspace_id: uuid.UUID
    ) -> list[AppShare]:
        """List all records actively shared out by this workspace ("my shares").

        Returns:
            List[AppShare]: Share records with source-app info
        """
        stmt = (
            select(AppShare)
            .where(
                AppShare.source_workspace_id == workspace_id,
                AppShare.is_active.is_(True)
            )
            .order_by(AppShare.created_at.desc())
        )
        return list(self.db.scalars(stmt).all())

    def update_share_permission(
            self,
            *,
            app_id: uuid.UUID,
            target_workspace_id: uuid.UUID,
            permission: str,
            workspace_id: uuid.UUID | None = None
    ) -> AppShare:
        """Update share permission (readonly <-> editable).

        Args:
            app_id: App ID
            target_workspace_id: Target workspace ID
            permission: New permission value, readonly | editable
            workspace_id: Current workspace ID (used for permission validation)

        Returns:
            AppShare: Updated share record
        """
        if permission not in ("readonly", "editable"):
            raise BusinessException(
                "权限值无效，只允许 readonly 或 editable", BizCode.INVALID_PARAMETER
            )

        app = self.app_service._get_app_or_404(app_id)
        self.app_service._validate_workspace_access(app, workspace_id)

        stmt = select(AppShare).where(
            AppShare.source_app_id == app_id,
            AppShare.target_workspace_id == target_workspace_id,
            AppShare.is_active.is_(True)
        )
        share = self.db.scalars(stmt).first()

        if not share:
            raise ResourceNotFoundException(
                "共享记录",
                f"app_id={app_id}, target_workspace_id={target_workspace_id}"
            )

        share.permission = permission
        share.updated_at = utcnow_naive()
        self.db.commit()
        self.db.refresh(share)

        logger.info(
            "共享权限已更新",
            extra={
                "app_id": str(app_id),
                "target_workspace_id": str(target_workspace_id),
                "permission": permission,
            }
        )
        return share

    # ==================== Serialization ====================

    def _share_schemas(
            self,
            shares: list[AppShare],
            with_ontology: bool
    ) -> list[app_schema.AppShare]:
        """Batch the per-row relationship lookups for share records."""
        if not shares:
            return []

        app_ids = {s.source_app_id for s in shares}
        apps = self.db.scalars(select(App).where(App.id.in_(app_ids))).all()
        apps_by_id = {a.id: a for a in apps}

        release_ids = {a.current_release_id for a in apps if a.current_release_id}
        release_versions: dict[uuid.UUID, str] = {}
        if release_ids:
            rows = self.db.execute(
                select(AppRelease.id, AppRelease.version_name).where(
                    AppRelease.id.in_(release_ids)
                )
            ).all()
            release_versions = {row.id: row.version_name for row in rows}

        workspaces = cross_domain_read.get_workspaces_by_ids(
            self.db, {s.target_workspace_id for s in shares}
        )

        items: list[app_schema.AppShare] = []
        for s in shares:
            source_app = apps_by_id.get(s.source_app_id)
            target_workspace = workspaces.get(s.target_workspace_id)
            item = app_schema.AppShare(
                id=s.id,
                source_app_id=s.source_app_id,
                source_workspace_id=s.source_workspace_id,
                target_workspace_id=s.target_workspace_id,
                shared_by=s.shared_by,
                permission=s.permission,
                created_at=s.created_at,
                updated_at=s.updated_at,
                source_app_name=source_app.name if source_app else None,
                source_app_type=source_app.type if source_app else None,
                source_app_version=(
                    release_versions.get(source_app.current_release_id)
                    if source_app else None
                ),
                source_app_is_active=source_app.is_active if source_app else None,
                target_workspace_name=target_workspace.name if target_workspace else None,
                target_workspace_icon=target_workspace.icon if target_workspace else None,
            )
            items.append(item)

        # Load the ontology after the rows are serialized: a remote binder ends
        # the transaction before its callback, which expires ORM objects.
        if with_ontology:
            ontology_map = self.app_service._load_ontology_map(list(app_ids))
            if ontology_map is not None:
                for item in items:
                    ontology = ontology_map.get(item.source_app_id)
                    item.ontology = (
                        app_schema.AppOntologyInfo(**ontology) if ontology else None
                    )
        return items

    def share_schemas(self, shares: list[AppShare]) -> list[app_schema.AppShare]:
        """Share records to schemas (no ontology; mirrors the monolith route behavior)."""
        return self._share_schemas(shares, with_ontology=False)

    def share_schemas_with_ontology(self, shares: list[AppShare]) -> list[app_schema.AppShare]:
        """Convert share records to schemas and fill in the source app's currently
        active ontology.

        When the same source app is shared to multiple workspaces the ontology
        is aggregated only once; in the community edition or on aggregation
        failure, ontology is None.
        """
        return self._share_schemas(shares, with_ontology=True)


__all__ = ["AppShareService"]
