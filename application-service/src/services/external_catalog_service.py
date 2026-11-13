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
from typing import Any

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
# 摘要枚举（旧客户端的 `status`）→ 精确上架状态（§1.1 固定映射）。
_STATUS_TO_LISTING = {
    "published": "listed",
    "unlisted": "pending_listing",
    "draft": "not_listed",
}


def _field(status: Any, name: str) -> Any:
    """网关结果可能是 dataclass 也可能是 dict（业界两种实现都见过）。"""

    if isinstance(status, dict):
        return status.get(name)
    return getattr(status, name, None)


def _review_status_of(status: Any) -> Any:
    """最新一次申请的审核状态：契约 §6.4 的正式名是 `review_status`。

    市场侧 `market_publication_service._resource_status_item` 只产出
    `review_status`；`latest_publication_status` 是早期实现的名字，没有任何
    上游会返回它，只作兜底。读取顺序必须与 `my_shares_service._market_rows`
    一致——两个入口给出不同的审核状态，前端就会一边显示「审核中」另一边显示
    「可投稿」。

    走 `_field` 而非属性访问：`HttpMarketGateway.publication_status` 直接
    透传 JSON，拿到的是 dict，`status.review_status` 会 AttributeError。

    判真值而不是 `is not None`：上游给了空串时应当继续兜底，若把 `""` 当状态
    返回，调用方会拿它去比 `_LATEST_PUBLICATION_STATUSES` 并抛
    `INVALID_UPSTREAM_STATUS` —— 一个缺字段的旧网关就这样把降级变成 500。

    这里**不**回落到摘要枚举 `status`（`published` / `unlisted` / `draft`）：
    那就不是审核状态了，而且会被同一份枚举校验拒成 500。`status` 的归宿是
    `_listing_status_of`，按 `_STATUS_TO_LISTING` 推上架状态。
    """

    review_status = _field(status, "review_status")
    if review_status:
        return review_status
    return _field(status, "latest_publication_status") or None


def _listing_status_of(status: Any) -> str | None:
    """精确上架状态：市场直接给就用，只给摘要枚举时按固定映射推导。

    v0.5 之后 `status` 已从市场契约移除，这里只是兼容尚未升级的市场服务：
    映射是 §1.1 规定的那一条，不做额外推断（「待上架」到底是发布方自己下架
    还是运营仅保存，只有 `delist_actor` 能回答，缺它就留空）。
    """

    listing_status = _field(status, "listing_status")
    if isinstance(listing_status, str) and listing_status:
        return listing_status
    return _STATUS_TO_LISTING.get(str(_field(status, "status") or ""))


def _request_type_of(status: Any) -> str | None:
    # 市场 §6.4 的批量状态接口字段名是 `latest_request_type`，早期实现叫
    # `request_type`，两个都认。
    request_type = _field(status, "latest_request_type") or _field(status, "request_type")
    if request_type == "new_listing":
        # 契约 v0.9 把首次上架改名为 `first_publish`，旧市场仍可能回旧枚举。
        request_type = "first_publish"
    if request_type in ("first_publish", "version_update"):
        return request_type
    if not _review_status_of(status):
        return None
    # 兜底推导：资源已经有在架版本 → 这次是新版本；否则是首次上架。
    return "version_update" if _field(status, "current_version") else "first_publish"


def _can_delist(status: Any) -> bool:
    value = _field(status, "can_delist")
    if value is not None:
        return bool(value)
    # 市场没给判定时按契约自行推导（§3.3）：在架即可下架，发布者身份由市场校验。
    return _listing_status_of(status) == "listed"


def _can_relist(status: Any) -> bool:
    value = _field(status, "can_relist")
    if value is not None:
        return bool(value)
    return (
        _listing_status_of(status) == "pending_listing"
        and _field(status, "delist_actor") == "publisher"
    )


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
        # 这一整段都用 `_field` 读：网关结果可能是 dataclass（企业侧替身）也可能
        # 是 dict（`HttpMarketGateway.publication_status` 透传 JSON），直接点属性
        # 在 dict 上就是 AttributeError——状态查询会 500 而不是降级。
        review_status = _review_status_of(status)
        resource_id_upstream = _field(status, "resource_id")
        updated_at = _field(status, "updated_at") or 0
        latest = None
        if review_status is not None:
            if (
                not isinstance(review_status, str)
                or review_status not in _LATEST_PUBLICATION_STATUSES
            ):
                raise ExternalCatalogError(
                    BizCode.EXTERNAL_CATALOG_INVALID,
                    "市场返回的发布状态不合法",
                    data={"reason": "INVALID_UPSTREAM_STATUS"},
                )
            latest = LatestPublication(
                id=resource_id_upstream or "",
                version=_field(status, "latest_version") or "",
                status=review_status,  # type: ignore[arg-type]
                submitted_at=updated_at,
                reviewed_at=None,
                review_note=None,
                reject_reason=None,
                # `request_type` 决定审核通过后的上架效果（§5.3）。市场侧的目标
                # 契约会直接给；给不出时按「资源已上架过 → 版本更新」推导，
                # 首次投稿（资源尚无 current_version）按 `new_listing`。
                request_type=_request_type_of(status),
            )
            if review_status == "pending":
                block_reason = "PENDING_REVIEW"
        resource = None
        if resource_id_upstream:
            listing_status = _listing_status_of(status)
            resource = PublicationResourceState(
                id=resource_id_upstream,
                listing_status=listing_status,
                delist_actor=_field(status, "delist_actor"),
                delisted_at=_field(status, "delisted_at"),
                delist_reason=_field(status, "delist_reason"),
                current_version=_field(status, "current_version"),
                install_count=0,
                updated_at=updated_at,
            )
        return PublicationStatusResponse(
            resource_type=resource_type,
            local_resource_id=resource_id,
            resource=resource,
            latest_publication=latest,
            can_publish=block_reason is None,
            can_delist=_can_delist(status),
            can_relist=_can_relist(status),
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
