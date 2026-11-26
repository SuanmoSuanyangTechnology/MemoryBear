from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from src.errors import BizCode, ResourceNotFoundException
from src.models import (
    App,
    AppAsset,
    AppAssetInstallation,
    AppRelease,
    AppShare,
    ToolConfig,
    WorkflowToolConfig,
)
from src.models.app_asset_model import LinkMode, ShareMode
from src.services.app_service import AppService
from src.services.asset_release_resolver import AssetReleaseResolver, AsyncAssetReleaseResolver
from src.services.asset_service import AssetService


@pytest.mark.asyncio
async def test_deleting_catalog_source_retires_detail_and_reference(monkeypatch):
    engine = create_engine("sqlite://")
    tables = [
        App,
        AppRelease,
        AppAsset,
        AppAssetInstallation,
        AppShare,
        ToolConfig,
        WorkflowToolConfig,
    ]
    App.metadata.create_all(engine, tables=[model.__table__ for model in tables])
    tenant_id, user_id, source_workspace, ref_workspace, copy_workspace = (
        uuid4() for _ in range(5)
    )
    monkeypatch.setattr(
        "src.repositories.cross_domain_read.get_user_display_names_by_ids",
        lambda db, user_ids: {},
    )
    monkeypatch.setattr(
        "src.repositories.cross_domain_read.get_workspace_tenant_id",
        lambda db, workspace_id: tenant_id,
    )
    # The ontology binder is a process-global plugin; keep this local
    # catalog-retirement contract off the remote callback channel.
    monkeypatch.setattr("src.services.app_service.get_plugin", lambda name: None)
    with Session(engine) as db:
        source = App(
            id=uuid4(),
            workspace_id=source_workspace,
            created_by=user_id,
            name="Shared agent",
            type="agent",
        )
        copy = App(
            id=uuid4(),
            workspace_id=copy_workspace,
            created_by=user_id,
            name="Local copy",
            type="agent",
        )
        release = AppRelease(
            id=uuid4(),
            app_id=source.id,
            version=1,
            version_name="1",
            name=source.name,
            type="agent",
            published_by=user_id,
        )
        asset = AppAsset(
            id=uuid4(),
            tenant_id=tenant_id,
            source_app_id=source.id,
            source_workspace_id=source_workspace,
            published_release_id=release.id,
            published_version=1,
        )
        reference = AppAssetInstallation(
            asset_id=asset.id,
            tenant_id=tenant_id,
            target_workspace_id=ref_workspace,
            permission=ShareMode.READONLY,
            link_mode=LinkMode.REFERENCE,
            installed_release_id=release.id,
        )
        installed_copy = AppAssetInstallation(
            asset_id=asset.id,
            tenant_id=tenant_id,
            target_workspace_id=copy_workspace,
            permission=ShareMode.EDITABLE,
            link_mode=LinkMode.COPY,
            installed_release_id=release.id,
            local_app_id=copy.id,
        )
        db.add_all([source, copy, release, asset, reference, installed_copy])
        db.commit()

        catalog = AssetService(db)
        resolver = AssetReleaseResolver(db)
        async_db = AsyncMock(spec=AsyncSession)
        async_db.scalar.side_effect = db.scalar
        async_db.execute.side_effect = db.execute
        async_resolver = AsyncAssetReleaseResolver(async_db)
        install_args = {
            "asset_id": asset.id,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "target_workspace_id": ref_workspace,
        }

        assert catalog.list_assets(tenant_id=tenant_id)[1] == 1
        assert catalog.get_asset(asset_id=asset.id, tenant_id=tenant_id)["view_count"] == 1
        assert catalog.install_asset(**install_args)["reused"] is True
        assert resolver.resolve(app_id=source.id, workspace_id=ref_workspace).source == "reference"
        assert (
            await async_resolver.resolve(app_id=source.id, workspace_id=ref_workspace)
        ).source == "reference"

        AppService(db).delete_app(app_id=source.id, workspace_id=source_workspace)

        assert catalog.list_assets(tenant_id=tenant_id) == ([], 0)
        with pytest.raises(ResourceNotFoundException) as detail_error:
            catalog.get_asset(asset_id=asset.id, tenant_id=tenant_id)
        assert detail_error.value.code == BizCode.FILE_NOT_FOUND
        with pytest.raises(ResourceNotFoundException) as install_error:
            catalog.install_asset(**install_args)
        assert install_error.value.code == BizCode.FILE_NOT_FOUND
        assert asset.view_count == 1
        assert not resolver.can_access(app_id=source.id, workspace_id=ref_workspace)
        assert not await async_resolver.can_access(app_id=source.id, workspace_id=ref_workspace)
        assert resolver.resolve(app_id=copy.id, workspace_id=copy_workspace).source == "own"
        assert (
            await async_resolver.resolve(app_id=copy.id, workspace_id=copy_workspace)
        ).source == "own"
