"""Tenant resource center (design doc §4.1 / §5 / §6.1).

Scope boundaries this module must keep (they are the whole point of the section):

- **No publish / unpublish endpoint.** Visibility follows the share switches: an
  entry exists because someone shared it, and disappears from the catalog when
  the sharer cancels. There is no state-only entry point. Operating the
  *community market* is a different chain (§6.3, `/sys/market/.../unlist`).
- **No version-forward endpoint.** Advancing a pinned release = re-opening the
  share dialog and sharing again with a newer `release_id`.
- **No sync / my-installs / preview.** A copy diverges from the source the moment
  it is created; "what did I pull" is visible in the space's own app list.

Everything shown in the detail page comes from the pinned release snapshot
(`app_releases`), never from the app draft, so a source edit cannot silently
change what consumers see.
"""
from __future__ import annotations

import copy
import logging
import uuid
from typing import Any

from sqlalchemy import cast, func, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from ..errors import BizCode, BusinessException, ResourceNotFoundException
from ..models import (
    AgentConfig,
    App,
    AppAsset,
    AppAssetInstallation,
    AppRelease,
    MultiAgentConfig,
    Skill,
    ToolConfig,
    WorkflowConfig,
)
from ..models.app_asset_model import (
    AssetStatus,
    InstallSource,
    InstallStatus,
    LinkMode,
    ShareMode,
)
from ..models.app_model import AppType
from ..repositories import cross_domain_read
from ..utils.datetime_utils import to_timestamp_ms, utcnow_naive
from .app_service import AppService, _expand_usage_examples
from .detail_view import build_display_payload

logger = logging.getLogger(__name__)


def _share_scope_error(reason: str, message: str) -> BusinessException:
    """One code (`1003`) with `data.reason`, so the code segment never grows."""

    return BusinessException(
        message,
        BizCode.INVALID_PARAMETER,
        data={"reason": reason},
    )


def _coerce_uuid(value: Any) -> uuid.UUID | None:
    if value is None:
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


class AssetService:
    """Catalog entries (switches 1/2) for the tenant resource center."""

    def __init__(self, db: Session, app_service: AppService | None = None):
        self.db = db
        self.app_service = app_service or AppService(db)

    # ==================== 共享（发布弹窗） ====================

    def share_to_catalog(
            self,
            *,
            app_id: uuid.UUID,
            user_id: uuid.UUID,
            workspace_id: uuid.UUID | None,
            tenant_id: uuid.UUID,
            release_id: uuid.UUID | None,
            enabled: bool,
            allow_editable: bool,
    ) -> AppAsset | None:
        """开关 1/2：把应用（指定版本）放进租户资源中心。

        Returns the entry, or None when switch 1 is off (the caller still has to
        handle switch 3, which is independent).
        """

        app = self.app_service._get_app_or_404(app_id)
        self.app_service._validate_workspace_access(app, workspace_id)
        self._assert_can_share(app, user_id, workspace_id)

        if not enabled:
            # 开关 1 关闭时开关 2 无意义（400 1003 + data.reason，§6.0）
            if allow_editable:
                raise _share_scope_error(
                    "INVALID_SHARE_SCOPE", "开关 1 关闭时开关 2 无意义"
                )
            # Switch 1 off: keep whatever the previous submission decided; the
            # sharer cancels explicitly through DELETE /assets/{id} (§5.4).
            return None

        release = self._resolve_release(app, release_id)
        return self._upsert_asset(
            app=app,
            release=release,
            user_id=user_id,
            tenant_id=tenant_id,
            allow_editable=allow_editable,
            visible=True,
        )

    def ensure_market_carrier(
            self,
            *,
            app_id: uuid.UUID,
            user_id: uuid.UUID,
            workspace_id: uuid.UUID | None,
            tenant_id: uuid.UUID,
            release_id: uuid.UUID | None,
            allow_editable: bool = False,
    ) -> tuple[AppAsset, AppRelease]:
        """开关 3 单独打开时的载体行。

        `market_sync.asset_id` 是指向 `app_assets` 的外键，而开关 3 可以与开关 1
        完全独立（§6.0「可以只开它一个」）。因此这里确保条目存在，但用
        `status=unpublished` 让它在资源中心不展示——行只承载市场指针，不改变
        「共享到资源中心」这个语义。已存在的条目**不降级**：开关 1 关闭时保留
        上一次的决定，取消共享走 `DELETE /assets/{id}`。
        """

        app = self.app_service._get_app_or_404(app_id)
        self.app_service._validate_workspace_access(app, workspace_id)
        self._assert_can_share(app, user_id, workspace_id)

        release = self._resolve_release(app, release_id)
        return self._upsert_asset(
            app=app,
            release=release,
            user_id=user_id,
            tenant_id=tenant_id,
            allow_editable=allow_editable,
            visible=False,
        ), release

    def _upsert_asset(
            self,
            *,
            app: App,
            release: AppRelease,
            user_id: uuid.UUID,
            tenant_id: uuid.UUID,
            allow_editable: bool,
            visible: bool,
    ) -> AppAsset:
        """建/更新条目。一个应用在一个租户内只有一个条目（唯一约束）。"""

        now = utcnow_naive()
        asset = self.db.scalars(
            select(AppAsset).where(
                AppAsset.tenant_id == tenant_id,
                AppAsset.source_app_id == app.id,
            )
        ).first()

        if asset is None:
            asset = AppAsset(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                source_app_id=app.id,
                source_workspace_id=app.workspace_id,
                published_release_id=release.id,
                published_version=release.version,
                share_mode=ShareMode.EDITABLE if allow_editable else ShareMode.READONLY,
                # 仅作市场指针载体时不可见；开关 1 打开时立刻可见
                status=AssetStatus.PUBLISHED if visible else AssetStatus.UNPUBLISHED,
                view_count=0,
                # 与迁移回填口径一致（`COALESCE(r.description, a.description)`）：
                # 版本快照是不可变的，改应用描述不会回写已发版本；用户在创建后
                # 才补的描述只存在于 `apps.description`，这里不回退就会漏掉。
                description=release.description or app.description,
                display_payload=self._display_payload(release),
                created_by=user_id,
                created_at=now,
                updated_at=now,
                is_active=True,
            )
            self.db.add(asset)
        else:
            # Re-sharing is idempotent on the same release and forwards the
            # version otherwise (§5.4). Re-sharing after an unshare restores
            # visibility on the release that was already pinned.
            if asset.published_release_id != release.id:
                asset.published_release_id = release.id
                asset.published_version = release.version
            asset.share_mode = (
                ShareMode.EDITABLE if allow_editable else ShareMode.READONLY
            )
            if visible and asset.status == AssetStatus.UNPUBLISHED:
                asset.status = AssetStatus.PUBLISHED
            asset.description = release.description or app.description
            asset.display_payload = self._display_payload(release)
            asset.updated_at = now
            asset.is_active = True

        self.db.flush()
        logger.info(
            "共享到资源中心",
            extra={
                "app_id": str(app.id),
                "asset_id": str(asset.id),
                "release_id": str(release.id),
                "share_mode": asset.share_mode,
                "visible": asset.status == AssetStatus.PUBLISHED,
            },
        )
        return asset

    def _assert_can_share(
            self, app: App, user_id: uuid.UUID, workspace_id: uuid.UUID | None
    ) -> None:
        """空间级决定：管理员或应用创建者才能把它放进租户资源中心。"""

        if workspace_id is None:
            raise BusinessException(
                "缺少工作空间上下文", BizCode.WORKSPACE_NO_ACCESS
            )
        if app.created_by == user_id:
            return
        if cross_domain_read.is_workspace_manager(self.db, user_id, workspace_id):
            return
        raise BusinessException(
            "只有空间管理员或应用创建者可以共享该应用",
            BizCode.PERMISSION_DENIED,
        )

    def _resolve_release(self, app: App, release_id: uuid.UUID | None) -> AppRelease:
        target_id = release_id or app.current_release_id
        if not target_id:
            raise BusinessException(
                "应用尚未发布，无法共享", BizCode.RELEASE_NOT_FOUND
            )
        release = self.db.scalars(
            select(AppRelease).where(
                AppRelease.id == target_id,
                AppRelease.app_id == app.id,
                AppRelease.is_active.is_(True),
            )
        ).first()
        if release is None:
            raise BusinessException(
                f"版本 {target_id} 不存在或已下线", BizCode.RELEASE_NOT_FOUND
            )
        return release

    def _display_payload(self, release: AppRelease) -> dict[str, Any]:
        """同租户内无需脱敏（§7.0）：真实配置 + 全部展示字段。"""

        return {
            "name": release.name,
            "description": release.description,
            "icon": release.icon,
            "icon_type": release.icon_type,
            "app_type": release.type,
            "version_name": release.version_name,
            "changelog": release.release_notes,
            "categories": list(release.categories or []),
            "usage_examples": _expand_usage_examples(release.usage_examples),
            "config": release.config or {},
        }

    # ==================== 取消共享 ====================

    def unshare_asset(
            self,
            *,
            asset_id: uuid.UUID,
            user_id: uuid.UUID,
            workspace_id: uuid.UUID | None,
            tenant_id: uuid.UUID,
    ) -> AppAsset:
        """条目置 `unpublished`：不再展示，引用型消费方立即失效，副本不受影响。"""

        asset = self._get_asset_or_404(asset_id, tenant_id)
        app = self.app_service._get_app_or_404(asset.source_app_id)
        self._assert_can_share(app, user_id, workspace_id)

        asset.status = AssetStatus.UNPUBLISHED
        asset.updated_at = utcnow_naive()
        self.db.commit()

        logger.info(
            "取消共享资源中心条目",
            extra={"asset_id": str(asset_id), "tenant_id": str(tenant_id)},
        )
        return asset

    # ==================== 改共享权限 ====================

    def update_share_mode(
            self,
            *,
            asset_id: uuid.UUID,
            tenant_id: uuid.UUID,
            share_mode: str,
            user_id: uuid.UUID,
            workspace_id: uuid.UUID | None,
    ) -> AppAsset:
        """切换条目的 `share_mode`（`readonly` ↔ `editable`）。

        「我的共享」那一列的权限开关就是它：之前的实现只有共享时一次性设定
        （`_upsert_asset` 里的 `allow_editable`），之后没有任何更新入口，前端
        开关点了不落库。

        只影响**之后**的 `install_asset`：形态由条目实时读取（`install_asset`
        里 `asset.share_mode == ShareMode.EDITABLE`），已产生的安装记录
        `app_asset_installations.permission` 是安装当时的快照，不追溯改写。
        """

        if share_mode not in (ShareMode.READONLY, ShareMode.EDITABLE):
            raise BusinessException(
                "权限值无效，只允许 readonly 或 editable",
                BizCode.INVALID_PARAMETER,
                data={"reason": "INVALID_SHARE_MODE"},
            )

        # `_get_asset_or_404` 已经挡掉不存在 / 已取消共享（unpublished）的条目：
        # 取消共享后列表里没有这一行，也没有可改的开关。
        asset = self._get_asset_or_404(asset_id, tenant_id)
        app = self.app_service._get_app_or_404(asset.source_app_id)
        self._assert_can_share(app, user_id, workspace_id)

        if asset.share_mode != share_mode:
            asset.share_mode = share_mode
            asset.updated_at = utcnow_naive()
            self.db.commit()
            logger.info(
                "更新资源中心条目共享权限",
                extra={
                    "asset_id": str(asset_id),
                    "tenant_id": str(tenant_id),
                    "share_mode": share_mode,
                },
            )
        return asset

    # ==================== 拉取到空间 ====================

    def install_asset(
            self,
            *,
            asset_id: uuid.UUID,
            tenant_id: uuid.UUID,
            user_id: uuid.UUID,
            target_workspace_id: uuid.UUID,
    ) -> dict[str, Any]:
        """把条目拉到目标空间。形态只由条目的 `share_mode` 决定（§6.1）。

        - `readonly` → 只读引用：不产生新 app，运行时读条目钉住的 release 快照
        - `editable` → 可编辑副本：以 release 快照为源在当前空间重建 app，
          集群连同全部子 Agent 一起落地（§7.0.1）

        已有安装时是幂等返回，**只有一个方向会换形态**：已装的是只读引用、条目现在
        放开成 `editable`，则旧安装行转 `removed`、新落一条 `copy`。反方向
        （`copy` → 条目改回 `readonly`）不动：副本是本空间的自有应用，条目改权限
        不能把它收回或降级。

        注意形态变更**只由消费者主动再次安装触发**：发布方改 `share_mode` 只写
        条目（`update_share_mode`），不会往任何消费方的空间里写东西。
        """

        asset = self._get_asset_or_404(asset_id, tenant_id)

        target_tenant = cross_domain_read.get_workspace_tenant_id(self.db, target_workspace_id)
        if target_tenant is None:
            raise ResourceNotFoundException("工作空间", str(target_workspace_id))
        if target_tenant != tenant_id:
            # 资源中心只在租户内可见，跨租户只能走社区市场
            raise BusinessException("只能拉取到同一租户下的空间", BizCode.WORKSPACE_ACCESS_DENIED)

        existing = self.db.scalars(
            select(AppAssetInstallation).where(
                AppAssetInstallation.asset_id == asset.id,
                AppAssetInstallation.target_workspace_id == target_workspace_id,
                AppAssetInstallation.status == InstallStatus.ACTIVE,
            )
        ).first()

        replaced_link_mode: str | None = None
        if existing is not None:
            if not (
                existing.link_mode == LinkMode.REFERENCE
                and asset.share_mode == ShareMode.EDITABLE
            ):
                # 无形态变化（含 copy 方向）：幂等返回，不碰已有安装
                return self._install_payload(existing, reused=True)

            # 先让旧行失效再建副本，顺序不能反：
            # 1. `_copy_from_release` 用 `_unique_app_name` 判重，而它把活跃
            #    reference 安装的快照名算作已占用（`app_service._unique_app_name`
            #    的 `ref_names`），先建副本会被加后缀叫成「X(1)」。
            # 2. `uq_asset_install` 是 `WHERE status='active'` 的部分唯一索引，
            #    旧行不先落成 `removed`，新的 copy 行插不进去。
            replaced_link_mode = existing.link_mode
            existing.status = InstallStatus.REMOVED
            existing.updated_at = utcnow_naive()
            self.db.flush()

        release = self.db.get(AppRelease, asset.published_release_id)
        if release is None or not release.is_active:
            raise BusinessException("条目钉住的版本已下线", BizCode.RELEASE_NOT_FOUND)

        now = utcnow_naive()
        missing: list[dict[str, Any]] = []
        local_app_id: uuid.UUID | None = None
        link_mode = LinkMode.REFERENCE

        if asset.share_mode == ShareMode.EDITABLE:
            link_mode = LinkMode.COPY
            local_app_id = self._copy_from_release(
                release=release,
                source_app_id=asset.source_app_id,
                target_workspace_id=target_workspace_id,
                user_id=user_id,
                missing=missing,
            )

        installation = AppAssetInstallation(
            id=uuid.uuid4(),
            asset_id=asset.id,
            source=InstallSource.CATALOG,
            tenant_id=tenant_id,
            target_workspace_id=target_workspace_id,
            permission=asset.share_mode,
            link_mode=link_mode,
            installed_release_id=release.id,
            local_app_id=local_app_id,
            # 只读引用恒跟随条目版本；副本天生脱钩（§5.3）
            follow_version=link_mode == LinkMode.REFERENCE,
            status=InstallStatus.ACTIVE,
            result="partial" if missing else "success",
            missing_report=missing or None,
            created_by=user_id,
            created_at=now,
            updated_at=now,
        )
        self.db.add(installation)
        self.db.commit()

        logger.info(
            "从资源中心拉取",
            extra={
                "asset_id": str(asset.id),
                "target_workspace_id": str(target_workspace_id),
                "link_mode": link_mode,
                "result": installation.result,
                "replaced_link_mode": replaced_link_mode,
            },
        )
        return self._install_payload(
            installation, reused=False, replaced_link_mode=replaced_link_mode
        )

    def _install_payload(
            self,
            installation: Any,
            *,
            reused: bool,
            replaced_link_mode: str | None = None,
    ) -> dict[str, Any]:
        return {
            "id": str(installation.id),
            "asset_id": str(installation.asset_id) if installation.asset_id else None,
            "source": installation.source,
            "link_mode": installation.link_mode,
            "permission": installation.permission,
            "local_app_id": (
                str(installation.local_app_id) if installation.local_app_id else None
            ),
            "installed_release_id": (
                str(installation.installed_release_id)
                if installation.installed_release_id else None
            ),
            "result": installation.result,
            # 副本恒可编辑；partial 表示有依赖需要人工补齐
            "needs_config": installation.result == "partial",
            "missing_dependencies": installation.missing_report or [],
            "reused": reused,
            # 本次安装顶掉的旧形态（`reference` → `copy` 升级时非空）。前端据此
            # 提示「已按可编辑副本重新添加」，避免用户以为点错了没反应。
            "replaced_link_mode": replaced_link_mode,
        }

    # ---------- 以 release 快照为源重建副本 ----------

    def _copy_from_release(
            self,
            *,
            release: AppRelease,
            source_app_id: uuid.UUID,
            target_workspace_id: uuid.UUID,
            user_id: uuid.UUID,
            missing: list[dict[str, Any]],
    ) -> uuid.UUID:
        """以**版本快照**为源建一个 app（与 `copy_app` 读草稿行的关键差异）。

        副本要脱离源独立存活：集群的全部子 Agent 必须落到同一空间，否则
        副本一发布就掉光子 Agent（`_locate_agent_app` 按 workspace 过滤）。
        """

        source_app = self.app_service._get_app_or_404(source_app_id)
        config = copy.deepcopy(release.config or {})
        now = utcnow_naive()
        is_cross_workspace = target_workspace_id != source_app.workspace_id

        new_app = App(
            id=uuid.uuid4(),
            workspace_id=target_workspace_id,
            created_by=user_id,
            name=self.app_service._unique_app_name(
                release.name, target_workspace_id, source_app.type
            ),
            description=release.description,
            icon=release.icon,
            icon_type=release.icon_type,
            type=release.type,
            visibility="private",
            status="draft",
            tags=list(source_app.tags or []) if source_app.workspace_id == target_workspace_id else [],
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        self.db.add(new_app)
        self.db.flush()

        if new_app.type == AppType.AGENT:
            knowledge_retrieval, memory = self._remap_ws_scoped(
                config, target_workspace_id, is_cross_workspace
            )
            self.db.add(
                AgentConfig(
                    id=uuid.uuid4(),
                    app_id=new_app.id,
                    system_prompt=config.get("system_prompt"),
                    default_model_config_id=release.default_model_config_id,
                    model_parameters=config.get("model_parameters"),
                    knowledge_retrieval=knowledge_retrieval,
                    memory=memory,
                    variables=config.get("variables") or [],
                    tools=config.get("tools") or [],
                    skills=config.get("skills") or {},
                    features=config.get("features") or {},
                    is_active=True,
                    created_at=now,
                    updated_at=now,
                )
            )
        elif new_app.type in (AppType.WORKFLOW, AppType.PURE_WORKFLOW):
            self.db.add(
                WorkflowConfig(
                    id=uuid.uuid4(),
                    app_id=new_app.id,
                    workflow_type=config.get("workflow_type") or "workflow",
                    nodes=config.get("nodes") or [],
                    edges=config.get("edges") or [],
                    variables=config.get("variables") or [],
                    environment_variables=config.get("environment_variables") or [],
                    execution_config=config.get("execution_config") or {},
                    features=config.get("features") or {},
                    triggers=config.get("triggers") or [],
                    is_active=True,
                    created_at=now,
                    updated_at=now,
                )
            )
        elif new_app.type == AppType.MULTI_AGENT:
            self._copy_cluster_config(
                new_app=new_app,
                release=release,
                config=config,
                target_workspace_id=target_workspace_id,
                user_id=user_id,
                is_cross_workspace=is_cross_workspace,
                missing=missing,
                now=now,
            )

        return new_app.id

    def _remap_ws_scoped(
            self,
            config: dict[str, Any],
            target_workspace_id: uuid.UUID,
            is_cross_workspace: bool,
    ) -> tuple[Any, Any]:
        """知识库与记忆是**空间级**，跨空间必须重映射；租户级资源原样复用。"""

        if not is_cross_workspace:
            return (
                copy.deepcopy(config.get("knowledge_retrieval")),
                copy.deepcopy(config.get("memory")),
            )
        target_tenant_id = cross_domain_read.get_workspace_tenant_id(
            self.db, target_workspace_id
        )
        _, kb_ids = self.app_service._collect_resource_ids_from_config(
            None, config.get("knowledge_retrieval")
        )
        _, available_kb_ids = self.app_service._preload_cross_workspace_resources(
            target_tenant_id, target_workspace_id, set(), kb_ids
        )
        return (
            self.app_service._clean_knowledge_retrieval(
                config.get("knowledge_retrieval"), available_kb_ids
            ),
            self.app_service._clean_memory_cross_workspace(
                config.get("memory"), target_workspace_id
            ),
        )

    def _copy_cluster_config(
            self,
            *,
            new_app: App,
            release: AppRelease,
            config: dict[str, Any],
            target_workspace_id: uuid.UUID,
            user_id: uuid.UUID,
            is_cross_workspace: bool,
            missing: list[dict[str, Any]],
            now: Any,
    ) -> None:
        """集群副本：遍历一遍即止（不递归），子 Agent 与主 Agent 落在同一空间。"""

        id_map: dict[str, str] = {}
        kept_sub_agents: list[dict[str, Any]] = []

        for entry in config.get("sub_agents") or []:
            sub_release_id = _coerce_uuid(entry.get("release_id"))
            sub_release = (
                self.db.get(AppRelease, sub_release_id) if sub_release_id else None
            )
            if sub_release is None or not sub_release.is_active:
                missing.append(
                    {
                        "kind": "sub_agent",
                        "name": entry.get("name"),
                        "reason": "子 Agent 的钉住版本不存在或已下线",
                    }
                )
                continue
            new_sub_app_id = self._copy_from_release(
                release=sub_release,
                source_app_id=sub_release.app_id,
                target_workspace_id=target_workspace_id,
                user_id=user_id,
                missing=missing,
            )
            id_map[str(sub_release.app_id)] = str(new_sub_app_id)
            item = {k: v for k, v in entry.items() if k != "_ref"}
            item["agent_id"] = str(new_sub_app_id)
            # 副本里的子 Agent 是本地草稿，只能跟随各自当前版本
            item["release_policy"] = "current"
            item["release_id"] = None
            kept_sub_agents.append(item)

        routing_rules = []
        for rule in config.get("routing_rules") or []:
            item = {k: v for k, v in rule.items() if k != "_ref"}
            mapped = id_map.get(str(item.get("target_agent_id")))
            if mapped is None:
                # 目标子 Agent 没落地，留着就是条悬空路由
                continue
            item["target_agent_id"] = mapped
            routing_rules.append(item)

        master_release_id = _coerce_uuid(config.get("master_agent_id"))
        # 副本是草稿、尚无自己的 release，而 `master_agent_id` 存的是主 Agent 自己的
        # release（`multi_agent_model.py:61`）。留着它会让副本的主管去读**源应用**的
        # 快照——既是错误的配置，也是源方私有内容的泄漏路径。所以两种模式下都清空；
        # 只有跨空间时才需要让用户知道要重新指定（同空间是常态，清空后照常可运行）。
        if master_release_id is not None:
            if is_cross_workspace:
                missing.append(
                    {
                        "kind": "master_agent",
                        "name": None,
                        "reason": "主 Agent 属于源应用，副本发布时会重新指向自身版本",
                    }
                )
            master_release_id = None

        self.db.add(
            MultiAgentConfig(
                id=uuid.uuid4(),
                app_id=new_app.id,
                master_agent_id=master_release_id,
                master_agent_name=config.get("master_agent_name"),
                default_model_config_id=release.default_model_config_id,
                model_parameters=config.get("model_parameters"),
                orchestration_mode=config.get("orchestration_mode") or "supervisor_loop",
                sub_agents=kept_sub_agents,
                routing_rules=routing_rules or None,
                execution_config=config.get("execution_config") or {},
                supervisor_config=copy.deepcopy(config.get("supervisor_config")),
                aggregation_strategy=config.get("aggregation_strategy") or "merge",
                is_active=True,
                created_at=now,
                updated_at=now,
            )
        )

    # ==================== 浏览与详情 ====================

    def list_assets(
            self,
            *,
            tenant_id: uuid.UUID,
            category: str | None = None,
            q: str | None = None,
            app_type: str | None = None,
            page: int = 1,
            pagesize: int = 10,
    ) -> tuple[list[dict[str, Any]], int]:
        """租户内浏览：分类命中数组任一元素即匹配；`q` 命中名称/描述/发布人。"""

        stmt = (
            select(AppAsset, App, AppRelease)
            .join(App, App.id == AppAsset.source_app_id)
            .join(AppRelease, AppRelease.id == AppAsset.published_release_id)
            .where(
                AppAsset.tenant_id == tenant_id,
                AppAsset.status == AssetStatus.PUBLISHED,
                AppAsset.is_active.is_(True),
                App.is_active.is_(True),
            )
        )
        if category:
            # `app_releases.categories` 是 **JSON** 列（不是 JSONB）。SQLAlchemy 的
            # `.contains()` 只在 JSONB 上翻译成包含运算符 `@>`；遇到 JSON 会退化成
            # `col LIKE '%' || :param || '%'`，而 Postgres 没有 `json ~~ text`
            # 这个运算符，于是整个列表接口 500（`UndefinedFunction`）。
            # 显式 cast 到 JSONB 才能拿到「数组含该元素」的语义，且不动列类型、
            # 无需迁移——存量数据照原样读。
            stmt = stmt.where(cast(AppRelease.categories, JSONB).contains([category]))
        if app_type:
            stmt = stmt.where(AppRelease.type == app_type)
        if q:
            like = f"%{q}%"
            stmt = stmt.where(
                or_(
                    AppRelease.name.ilike(like),
                    AppRelease.description.ilike(like),
                )
            )

        total = self.db.scalar(
            select(func.count()).select_from(stmt.subquery())
        ) or 0

        rows = self.db.execute(
            stmt.order_by(AppAsset.updated_at.desc())
            .offset((page - 1) * pagesize)
            .limit(pagesize)
        ).all()

        items = [self._serialize_card(asset, app, release) for asset, app, release in rows]
        self._fill_publisher_names(items, rows)
        self._fill_install_counts(items, tenant_id)
        if q:
            # Publisher names are not filterable in SQL (users is a cross-domain
            # table with no relation here); filter them in the page payload and
            # keep the SQL count as the upper bound. Documented in §6.1.
            lowered = q.lower()
            items = [
                item
                for item in items
                if lowered in (item.get("source_app_name") or "").lower()
                or lowered in (item.get("description") or "").lower()
                or lowered in (item.get("publisher_name") or "").lower()
            ]
        return items, total

    def get_asset(
            self, *, asset_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> dict[str, Any]:
        """详情：展示字段 + 关联技能/工具；打开即 +1 浏览（§6.1.1 口径 1）。"""

        asset = self._get_asset_or_404(asset_id, tenant_id)
        app = self.app_service._get_app_or_404(asset.source_app_id)
        release = self.db.get(AppRelease, asset.published_release_id)

        asset.view_count = (asset.view_count or 0) + 1
        self.db.commit()

        payload = asset.display_payload or {}
        publisher = cross_domain_read.get_user_display_names_by_ids(
            self.db, {release.published_by} if release and release.published_by else set()
        )
        linked_skills, linked_tools = self._linked_resources(release)
        app_type = release.type if release else app.type
        config = payload.get("config") or {}

        return {
            "id": str(asset.id),
            "source_app_id": str(asset.source_app_id),
            "source_app_name": release.name if release else app.name,
            "source_app_type": release.type if release else app.type,
            "description": asset.description,
            "catalog_description": app.description,
            "icon": (payload.get("icon") or None),
            "icon_type": (payload.get("icon_type") or None),
            "published_version": asset.published_version,
            "published_version_name": payload.get("version_name"),
            "source_app_version": self._source_app_version(app),
            "has_newer_version": self._has_newer_version(asset, app),
            "view_count": asset.view_count,
            "publisher_name": next(iter(publisher.values()), None),
            "share_mode": asset.share_mode,
            "categories": payload.get("categories") or [],
            "usage_examples": payload.get("usage_examples") or [],
            "changelog": payload.get("changelog"),
            "linked_skills": linked_skills,
            "linked_tools": linked_tools,
            "install_count": self._count_installations(asset.id, tenant_id),
            "my_installations": self._my_installations(asset.id, tenant_id),
            # 详情页图形区（§6.1.3）。不要在这里直接返回 `config`：节点 config 里
            # 有 `url` / `headers` / `model_id` / `knowledge_bases`，跨租户不安全；
            # agent 也不展示配置。图形区只给「画图要画的东西」。
            "display_payload": build_display_payload(app_type, config),
            "updated_at": to_timestamp_ms(asset.updated_at),
        }

    def _serialize_card(
            self, asset: AppAsset, app: App, release: AppRelease
    ) -> dict[str, Any]:
        payload = asset.display_payload or {}
        return {
            "id": str(asset.id),
            "source_app_id": str(asset.source_app_id),
            "source_app_name": release.name,
            "source_app_type": release.type,
            "description": asset.description,
            "icon": payload.get("icon") or None,
            "icon_type": payload.get("icon_type") or None,
            "published_version": asset.published_version,
            "source_app_version": self._source_app_version(app),
            "has_newer_version": self._has_newer_version(asset, app),
            "view_count": asset.view_count or 0,
            "share_mode": asset.share_mode,
            "categories": payload.get("categories") or [],
            "publisher_name": None,
            "updated_at": to_timestamp_ms(asset.updated_at),
        }

    def _source_app_version(self, app: App) -> int | None:
        if not app.current_release_id:
            return None
        return self.db.scalar(
            select(AppRelease.version).where(AppRelease.id == app.current_release_id)
        )

    def _has_newer_version(self, asset: AppAsset, app: App) -> bool:
        latest = self._source_app_version(app)
        return latest is not None and latest > asset.published_version

    def _fill_publisher_names(
            self, items: list[dict[str, Any]], rows: list[Any]
    ) -> None:
        publisher_ids = {
            release.published_by
            for _asset, _app, release in rows
            if release.published_by
        }
        names = cross_domain_read.get_user_display_names_by_ids(self.db, publisher_ids)
        for item, (_asset, _app, release) in zip(items, rows):
            item["publisher_name"] = names.get(release.published_by)

    def _count_installations(self, asset_id: uuid.UUID, tenant_id: uuid.UUID) -> int:
        from ..models import AppAssetInstallation

        return self.db.scalar(
            select(func.count())
            .select_from(AppAssetInstallation)
            .where(
                AppAssetInstallation.asset_id == asset_id,
                AppAssetInstallation.tenant_id == tenant_id,
                AppAssetInstallation.status == InstallStatus.ACTIVE,
            )
        ) or 0

    def _fill_install_counts(
            self, items: list[dict[str, Any]], tenant_id: uuid.UUID
    ) -> None:
        from ..models import AppAssetInstallation

        if not items:
            return
        ids = [uuid.UUID(item["id"]) for item in items]
        counts = dict(
            self.db.execute(
                select(
                    AppAssetInstallation.asset_id,
                    func.count(),
                )
                .where(
                    AppAssetInstallation.asset_id.in_(ids),
                    AppAssetInstallation.tenant_id == tenant_id,
                    AppAssetInstallation.status == InstallStatus.ACTIVE,
                )
                .group_by(AppAssetInstallation.asset_id)
            ).all()
        )
        for item in items:
            item["install_count"] = counts.get(uuid.UUID(item["id"]), 0)

    def _my_installations(
            self, asset_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> list[dict[str, Any]]:
        from ..models import AppAssetInstallation

        rows = self.db.scalars(
            select(AppAssetInstallation).where(
                AppAssetInstallation.asset_id == asset_id,
                AppAssetInstallation.tenant_id == tenant_id,
                AppAssetInstallation.status == InstallStatus.ACTIVE,
            )
        ).all()
        return [
            {
                "id": str(row.id),
                "target_workspace_id": str(row.target_workspace_id),
                "link_mode": row.link_mode,
                "local_app_id": str(row.local_app_id) if row.local_app_id else None,
                "installed_version": self._installed_version(row),
            }
            for row in rows
        ]

    def _installed_version(self, row: Any) -> int | None:
        if not row.installed_release_id:
            return None
        return self.db.scalar(
            select(AppRelease.version).where(AppRelease.id == row.installed_release_id)
        )

    def _linked_resources(
            self, release: AppRelease | None
    ) -> tuple[list[str], list[str]]:
        """该版本实际引用到的技能与工具名称（§6.1.1 口径 2）。"""

        if release is None or not release.config:
            return [], []
        config = release.config or {}
        candidate_configs = [config]
        supervisor = config.get("supervisor_config")
        if isinstance(supervisor, dict):
            candidate_configs.append(supervisor)

        tool_ids: list[uuid.UUID] = []
        skill_ids: list[uuid.UUID] = []
        for cfg in candidate_configs:
            for tool in cfg.get("tools") or []:
                if not isinstance(tool, dict) or not tool.get("enabled"):
                    continue
                tool_id = _coerce_uuid(tool.get("tool_id"))
                if tool_id and tool_id not in tool_ids:
                    tool_ids.append(tool_id)
            skills = cfg.get("skills")
            if isinstance(skills, dict):
                for raw in skills.get("skill_ids") or []:
                    skill_id = _coerce_uuid(raw)
                    if skill_id and skill_id not in skill_ids:
                        skill_ids.append(skill_id)

        skill_names: list[str] = []
        if skill_ids:
            skill_names = [
                name
                for name in self.db.scalars(
                    select(Skill.name).where(Skill.id.in_(skill_ids))
                ).all()
                if name
            ]
        tool_names: list[str] = []
        if tool_ids:
            tool_names = [
                name
                for name in self.db.scalars(
                    select(ToolConfig.name).where(ToolConfig.id.in_(tool_ids))
                ).all()
                if name
            ]
        return skill_names, tool_names

    def _get_asset_or_404(self, asset_id: uuid.UUID, tenant_id: uuid.UUID) -> AppAsset:
        asset = self.db.scalars(
            select(AppAsset).where(
                AppAsset.id == asset_id,
                AppAsset.tenant_id == tenant_id,
                AppAsset.is_active.is_(True),
            )
        ).first()
        if asset is None:
            raise ResourceNotFoundException("资源中心条目", str(asset_id))
        if asset.status != AssetStatus.PUBLISHED:
            raise BusinessException(
                "该资源已取消共享", BizCode.WORKSPACE_ACCESS_DENIED
            )
        return asset


__all__ = ["AssetService"]