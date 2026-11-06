"""External catalog facade: publish, withdraw, status, install, uninstall.

The service is sync (management plane). Async gateway calls are bridged with
``asyncio.run``; the routes are regular ``def`` handlers, matching the tools
routes' existing bridge.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid

from sqlalchemy.orm import Session

from ..config import AppServiceSettings
from ..errors import BizCode
from ..external_catalog.bundle import (
    canonical_request_fingerprint,
    validate_bundle_shell,
)
from ..external_catalog.errors import ExternalCatalogError
from ..external_catalog.idempotency import IdempotencyStore
from ..external_catalog.orchestrator import build_manifest
from ..external_catalog.protocols import InstallContext, PublishContext
from ..external_catalog.registry import AdapterRegistry, default_registry
from ..models.workspace_model import Workspace
from ..protocols.market_gateway import (
    MarketGateway,
    MarketPublicationRequest,
)
from ..schemas.external_catalog_schema import (
    InstallRequest,
    InstallResponse,
    LatestPublication,
    PendingBinding,
    PublicationRequest,
    PublicationResourceState,
    PublicationResponse,
    PublicationStatusResponse,
    UninstallResponse,
    WithdrawResponse,
)

logger = logging.getLogger(__name__)

_IDEMPOTENCY_KEY_MIN = 8
_IDEMPOTENCY_KEY_MAX = 128
_LATEST_PUBLICATION_STATUSES = frozenset(
    {"pending", "approved", "rejected", "withdrawn"}
)
_RESOURCE_STATUSES = frozenset({"draft", "published", "unlisted"})


def _run(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError(
        "ExternalCatalogService is sync; call it from the management plane"
    )


class ExternalCatalogService:
    def __init__(
        self,
        db: Session | None,
        gateway: MarketGateway,
        settings: AppServiceSettings,
        registry: AdapterRegistry | None = None,
        idempotency: IdempotencyStore | None = None,
    ) -> None:
        self.db = db
        self.gateway = gateway
        self.settings = settings
        self.registry = registry or default_registry
        self.idempotency = idempotency

    def _ensure_enabled(self) -> None:
        if self.settings.application_service_market_mode != "gateway":
            raise ExternalCatalogError(
                BizCode.FEATURE_NOT_AVAILABLE,
                "社区市场未启用",
                data={"reason": "MARKET_DISABLED"},
            )

    def _publish_context(
        self,
        tenant_id: uuid.UUID,
        request: PublicationRequest,
    ) -> PublishContext:
        return PublishContext(
            db=self.db,
            tenant_id=tenant_id,
            local_resource_id=request.resource_id,
            release_id=request.release_id,
            version=request.version,
            display=request.display.model_dump(mode="json"),
        )

    def publish(
        self,
        tenant_id: uuid.UUID,
        actor_id: uuid.UUID,
        request: PublicationRequest,
        idempotency_key: str | None,
    ) -> PublicationResponse:
        self._ensure_enabled()
        fingerprint = canonical_request_fingerprint(
            request.model_dump(mode="json", exclude_none=True)
        )
        reservation = None
        if idempotency_key:
            self._validate_idempotency_key(idempotency_key)
            if self.idempotency is None:
                raise RuntimeError("idempotency store is not configured")
            reservation = self.idempotency.begin(
                "publish", str(tenant_id), str(actor_id), idempotency_key, fingerprint
            )
            if reservation.state == "replay":
                return PublicationResponse.model_validate(reservation.response)
            self._raise_idempotency_conflict(reservation.state)

        try:
            adapter = self.registry.get_publish(request.resource_type.value)
            ctx = self._publish_context(tenant_id, request)
            built = adapter.build_payload(ctx)
            display = request.display.model_dump(mode="json")
            if request.subtype and request.subtype != built.subtype:
                raise ExternalCatalogError(
                    BizCode.EXTERNAL_CATALOG_INVALID,
                    "资源子类型与请求不一致",
                    data={
                        "reason": "SUBTYPE_MISMATCH",
                        "expected": built.subtype,
                        "actual": request.subtype,
                    },
                )
            manifest = build_manifest(
                resource_type=request.resource_type.value,
                local_resource_id=request.resource_id,
                release_id=request.release_id
                if request.resource_type.value == "app"
                else None,
                display=display,
                built=built,
            )
            result = _run(
                self.gateway.submit_publication(
                    MarketPublicationRequest(
                        tenant_id=str(tenant_id),
                        actor_id=str(actor_id),
                        resource_type=request.resource_type.value,
                        subtype=built.subtype,
                        local_resource_id=request.resource_id,
                        release_id=request.release_id,
                        version=built.version,
                        display=display,
                        bundle=manifest,
                    )
                )
            )
            response = PublicationResponse(
                publication_id=result.publication_id,
                resource_id=result.resource_id,
                local_resource_id=request.resource_id,
                resource_type=request.resource_type.value,
                subtype=built.subtype,
                version=result.version,
                release_id=request.release_id,
                status="pending",
                content_hash=manifest["content_hash"],
                submitted_at=result.submitted_at,
            )
            if idempotency_key and self.idempotency is not None:
                self.idempotency.complete(
                    "publish",
                    str(tenant_id),
                    str(actor_id),
                    idempotency_key,
                    fingerprint,
                    response.model_dump(mode="json"),
                )
            return response
        except Exception:
            if idempotency_key and self.idempotency is not None:
                self.idempotency.abort(
                    "publish",
                    str(tenant_id),
                    str(actor_id),
                    idempotency_key,
                    fingerprint,
                )
            raise

    def withdraw(
        self, tenant_id: uuid.UUID, actor_id: uuid.UUID, publication_id: str
    ) -> WithdrawResponse:
        self._ensure_enabled()
        result = _run(
            self.gateway.withdraw_publication(
                publication_id, str(tenant_id), str(actor_id)
            )
        )
        return WithdrawResponse(
            publication_id=result.publication_id,
            status="withdrawn",
            withdrawn_at=result.withdrawn_at,
        )

    def publication_status(
        self, tenant_id: uuid.UUID, resource_type: str, resource_id: str
    ) -> PublicationStatusResponse:
        self._ensure_enabled()
        adapter = self.registry.get_publish(resource_type)
        ctx = PublishContext(
            db=self.db,
            tenant_id=tenant_id,
            local_resource_id=resource_id,
            release_id=None,
            version=None,
            display={},
        )
        block_reason = adapter.publish_block_reason(ctx)
        statuses = _run(
            self.gateway.publication_status(
                resource_type, [resource_id], str(tenant_id)
            )
        )
        if not statuses:
            return PublicationStatusResponse(
                resource_type=resource_type,
                local_resource_id=resource_id,
                resource=None,
                latest_publication=None,
                can_publish=block_reason is None,
                block_reason=block_reason,  # type: ignore[arg-type]
            )
        status = statuses[0]
        latest = None
        if status.latest_publication_status is not None:
            if (
                not isinstance(status.latest_publication_status, str)
                or status.latest_publication_status not in _LATEST_PUBLICATION_STATUSES
            ):
                raise ExternalCatalogError(
                    BizCode.EXTERNAL_CATALOG_INVALID,
                    "市场返回的发布状态不合法",
                    data={"reason": "INVALID_UPSTREAM_STATUS"},
                )
            latest = LatestPublication(
                id=status.resource_id or "",
                version=status.latest_version or "",
                status=status.latest_publication_status,  # type: ignore[arg-type]
                submitted_at=status.updated_at or 0,
                reviewed_at=None,
                review_note=None,
                reject_reason=None,
            )
            if status.latest_publication_status == "pending":
                block_reason = "PENDING_REVIEW"
        resource = None
        if status.resource_id:
            if (
                not isinstance(status.status, str)
                or status.status not in _RESOURCE_STATUSES
            ):
                raise ExternalCatalogError(
                    BizCode.EXTERNAL_CATALOG_INVALID,
                    "市场返回的资源状态不合法",
                    data={"reason": "INVALID_UPSTREAM_STATUS"},
                )
            resource = PublicationResourceState(
                id=status.resource_id,
                status=status.status,  # type: ignore[arg-type]
                current_version=status.current_version,
                install_count=0,
                updated_at=status.updated_at or 0,
            )
        return PublicationStatusResponse(
            resource_type=resource_type,
            local_resource_id=resource_id,
            resource=resource,
            latest_publication=latest,
            can_publish=block_reason is None,
            block_reason=block_reason,  # type: ignore[arg-type]
        )

    def install(
        self,
        tenant_id: uuid.UUID,
        actor_id: uuid.UUID,
        request: InstallRequest,
        idempotency_key: str,
    ) -> InstallResponse:
        self._ensure_enabled()
        self._validate_idempotency_key(idempotency_key)
        if self.idempotency is None:
            raise RuntimeError("idempotency store is not configured")
        fingerprint = canonical_request_fingerprint(
            request.model_dump(mode="json", exclude_none=True)
        )
        reservation = self.idempotency.begin(
            "install", str(tenant_id), str(actor_id), idempotency_key, fingerprint
        )
        if reservation.state == "replay":
            response = InstallResponse.model_validate(reservation.response)
            return response.model_copy(update={"idempotent_replay": True})
        self._raise_idempotency_conflict(reservation.state)

        committed = False
        try:
            workspace_id = self._resolve_install_workspace(
                tenant_id, request.workspace_id
            )
            bundle = _run(
                self.gateway.download_bundle(
                    request.resource_id,
                    request.version,
                    str(tenant_id),
                    str(actor_id),
                )
            )
            self._validate_downloaded_bundle(
                bundle,
                request.resource_type.value,
                request.resource_id,
                request.version,
            )
            adapter = self.registry.get_install(request.resource_type.value)
            installed = adapter.install(
                InstallContext(
                    db=self.db,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    workspace_id=workspace_id,
                    installed_by=str(actor_id),
                    bundle=bundle.bundle,
                    dependency=None,
                    id_map={},
                    market_resource_id=request.resource_id,
                    redacted_secrets=bundle.bundle.get("redacted_secrets", []),
                )
            )
            installed_at = int(time.time() * 1000)
            response = InstallResponse(
                resource_type=request.resource_type.value,
                resource_id=request.resource_id,
                version=request.version,
                local_resource_id=installed.local_resource_id,
                local_name=installed.local_name,
                workspace_id=str(workspace_id) if workspace_id else None,
                installed_at=installed_at,
                pending_bindings=[
                    PendingBinding.model_validate(item)
                    for item in installed.pending_bindings
                ],
                idempotent_replay=False,
            )
            # 先提交本地安装，再做 best-effort 登记；登记失败不回滚。
            if self.db is not None:
                self.db.commit()
            committed = True
            try:
                self.idempotency.complete(
                    "install",
                    str(tenant_id),
                    str(actor_id),
                    idempotency_key,
                    fingerprint,
                    response.model_dump(mode="json"),
                )
            except Exception:
                # The install is already committed. Leave the key in progress
                # instead of deleting it, so a retry cannot install twice.
                logger.exception("failed to cache install idempotency response")
            registration_synced = self._register_installation(
                tenant_id, actor_id, request, installed.local_resource_id, workspace_id
            )
            if not registration_synced:
                logger.warning("market installation registration failed")
            return response
        except Exception:
            if not committed:
                self.idempotency.abort(
                    "install",
                    str(tenant_id),
                    str(actor_id),
                    idempotency_key,
                    fingerprint,
                )
            raise

    def uninstall(
        self,
        tenant_id: uuid.UUID,
        actor_id: uuid.UUID,
        resource_type: str,
        local_resource_id: str,
        *,
        deregister_failure_is_fatal: bool = False,
    ) -> UninstallResponse:
        self._ensure_enabled()
        if resource_type == "app":
            raise ExternalCatalogError(
                BizCode.EXTERNAL_CATALOG_INVALID,
                "应用请使用应用删除接口",
                data={"reason": "USE_APP_DELETE"},
            )
        adapter = self.registry.get_install(resource_type)
        adapter.uninstall(self.db, tenant_id, local_resource_id)
        if self.db is not None:
            self.db.commit()
        synced = True
        try:
            _run(
                self.gateway.deregister_installation(
                    resource_type,
                    local_resource_id,
                    str(tenant_id),
                    str(actor_id),
                )
            )
        except Exception as exc:
            synced = False
            if deregister_failure_is_fatal:
                raise
            logger.warning("market deregistration failed: %s", exc)
        return UninstallResponse(
            resource_type=resource_type,
            local_resource_id=local_resource_id,
            uninstalled=True,
            registration_synced=synced,
        )

    def _resolve_install_workspace(
        self, tenant_id: uuid.UUID, workspace_id: str | None
    ) -> uuid.UUID | None:
        if workspace_id is None:
            return None
        workspace = self.db.get(Workspace, uuid.UUID(workspace_id))
        if workspace is None:
            raise ExternalCatalogError(
                BizCode.WORKSPACE_NOT_FOUND, "工作空间不存在"
            )
        if workspace.tenant_id != tenant_id:
            raise ExternalCatalogError(
                BizCode.WORKSPACE_ACCESS_DENIED, "无权访问该工作空间"
            )
        return workspace.id

    @staticmethod
    def _validate_downloaded_bundle(
        bundle, resource_type: str, resource_id: str, version: str
    ) -> None:
        if (bundle.resource_id and bundle.resource_id != resource_id) or (
            bundle.version and bundle.version != version
        ):
            raise ExternalCatalogError(
                BizCode.EXTERNAL_CATALOG_INVALID,
                "bundle 与安装请求不一致",
                data={"reason": "BUNDLE_INVALID"},
            )
        validate_bundle_shell(bundle.bundle, resource_type)
        if bundle.content_hash != bundle.bundle.get("content_hash"):
            raise ExternalCatalogError(
                BizCode.EXTERNAL_CATALOG_INVALID,
                "bundle 哈希不一致",
                data={"reason": "CONTENT_HASH_MISMATCH"},
            )

    def _register_installation(
        self,
        tenant_id: uuid.UUID,
        actor_id: uuid.UUID,
        request: InstallRequest,
        local_resource_id: str,
        workspace_id: uuid.UUID | None,
    ) -> bool:
        from ..protocols.market_gateway import MarketInstallationRequest

        try:
            _run(
                self.gateway.register_installation(
                    MarketInstallationRequest(
                        tenant_id=str(tenant_id),
                        actor_id=str(actor_id),
                        resource_id=request.resource_id,
                        version=request.version,
                        resource_type=request.resource_type.value,
                        local_resource_id=local_resource_id,
                        workspace_id=str(workspace_id) if workspace_id else None,
                        installed_by=str(actor_id),
                    )
                )
            )
            return True
        except Exception as exc:
            logger.warning("market installation registration failed: %s", exc)
            return False

    @staticmethod
    def _validate_idempotency_key(key: str) -> None:
        if not _IDEMPOTENCY_KEY_MIN <= len(key) <= _IDEMPOTENCY_KEY_MAX:
            raise ExternalCatalogError(
                BizCode.EXTERNAL_CATALOG_INVALID,
                "Idempotency-Key 长度必须为 8-128 字符",
                data={"reason": "IDEMPOTENCY_KEY_INVALID"},
            )

    @staticmethod
    def _raise_idempotency_conflict(state: str) -> None:
        if state == "in_progress":
            raise ExternalCatalogError(
                BizCode.EXTERNAL_CATALOG_CONFLICT,
                "相同幂等键的请求仍在处理中",
                data={"reason": "IDEMPOTENCY_IN_PROGRESS"},
            )
        if state == "mismatch":
            raise ExternalCatalogError(
                BizCode.EXTERNAL_CATALOG_CONFLICT,
                "相同幂等键已用于不同请求",
                data={"reason": "IDEMPOTENCY_KEY_REUSED"},
            )


__all__ = ["ExternalCatalogService"]
